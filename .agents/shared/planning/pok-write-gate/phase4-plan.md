# Wave 1 · Phase 4: kb-writer Grant Removal + Compensating Capture Duty (rider RESTORED)

## Objective
Emperor direct ruling (addendum-3 item 1, overriding addendum-2's retraction): kb-writer LOSES the `pok` grant (54→53). The one-fact-one-store routing contract and capture duty MIGRATE to the 4 operational agents (ari, project-manager, leader, devops). Corrected rails apply. NO `tool-categories.json` change, NO rider baseline regen (meta.json unsealed).

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Remove `"pok"` from `agents/kb-writer/meta.json` tools.allow (54→53) | Phase 0 | grant-pin green at 53 |
| 2 | Update `test_pok_grant_pins.py`: EXPECTED_EXPLICIT 54→53 + completeness-walk exception for kb-writer | Phase 0 | pin green |
| 3 | Refresh stale docstring in grant-pin file | T2 | docstring matches |
| 4 | NEW pin: kb-writer does NOT hold `pok` + mutation leg (re-add → RED) | T2 | new pin green + mutation RED |
| 5 | Verify `tool-categories.json` UNTOUCHED (no regen/version bump) | none | git diff clean on artifact + registry |
| 6 | Compensating capture-duty guidance for 4 operational agents (ari, PM, leader, devops) — operational lessons captured in POK with reason; edits land in each agent's `tools_note.md` | Phase 0 | 4 sealed `.md` edits (`agents/{ari,project-manager,leader,devops}/tools_note.md`) |
| 7 | Migrate routing contract to these 4 (operational→POK with reason, knowledge→RAG) | T6 | pin + mutation leg |
| 8 | Baseline regen for the 4 edited sealed entries (`agents/ari/tools_note.md`, `agents/project-manager/tools_note.md`, `agents/leader/tools_note.md`, `agents/devops/tools_note.md`) | T6/T7 | `--check` rc=0 |
| 9 | Pin-tests for touched sealed files | T8 | green |
| 10 | kb-writer keeps RAG-side routing only (no POK guidance added) | T1 | grep confirms |

## Coupling
- **Independent of:** Phase 3 (reason param) and Phase 2 (family-count coordination) — rider retraction reversal decouples this lane.
- **Loose with:** Phase 9 (no separate writer guidance — migrated here).

## Risks
- DIVERGENCE A RESOLVED — Emperor supersedes both architect §4.3 and addendum-2 retraction. Grant removal is binding.
- meta.json unsealed → no rider baseline regen; but 4-agent sealed edits DO need regen.
- tool-categories.json = vocabulary → no regen/version bump.

## Exit Criterion
Grant removed (53/55); pins updated + new absence pin + mutation RED; tool-categories untouched; 4 agents carry capture duty + routing contract; baseline rc=0; kb-writer RAG-only.
