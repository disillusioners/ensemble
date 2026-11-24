# Wave 1 · Phase 0: Preflight + Anchor Re-verification + Grant Census

## Objective
Verify every anchor cited in design-amendments-v2.md and plan-overview.md against the worktree; confirm the kb-writer grant is PRESENT (54→53 removal target), `meta.json` unsealed, and tool-categories = vocabulary. Read-only.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Re-verify family-count anchor: `repository.py:221` `count_active_in_family` + `:250` `startswith(f"{family}-")` (addendum's `:114-122` stale); **plus the three B-1 retrieval-leg anchors, content-matched at worktree HEAD `48abbce67`: `repository.py:378` `row.access_count = (row.access_count or 0) + 1` (sole increment), `pok_service.py:733` `self._repo.bump_access(...)` (sole caller), `pok_injection.py:199` `-(row.access_count or 0),` (rank-key read); the drifted `:232`/`:677`/`:198` are REJECTED** | none | All anchors resolve by content; drift documented |
| 2 | Re-verify `record_verification` at `repository.py:384` (addendum's `:253` stale), zero callers — now to be wired | none | grep confirms definition only |
| 3 | Confirm kb-writer still holds `pok` grant; census 54/55 pre-edit (removal target 53) | none | grant-pin green at 54/55 |
| 4 | Confirm `meta.json` unsealed (0 matches in `agents/.prompt-artifacts.sha256`) | none | grep returns 0 — no rider baseline regen |
| 5 | Confirm `tool-categories.json` = vocabulary (66/V11), not grants | none | inspection confirms — no regen/version bump |

## Coupling
- **Independent of:** all phases (preflight).

## Risks
- Anchor drift — re-grep at use time; two stale anchors already found (`:114-122`, `:253`).

## Exit Criterion
All anchors verified with drift noted; grant 54/55 confirmed pre-edit; meta.json unsealed confirmed; tool-categories vocabulary confirmed. No writes.
