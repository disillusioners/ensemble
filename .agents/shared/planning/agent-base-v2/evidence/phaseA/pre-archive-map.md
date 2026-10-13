# BEFORE/AFTER MAP — Phase A final artifact (FINAL — addendum-resolved)

This file is the frozen before/after artifact documenting WHAT EXISTS WHERE for the opencode stack removal.

## Scope sources
- Source: `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP"
- Authoritative classification: exploration addendum (2026-10-13).

## Terminal states observed

| State | Row count | Notes |
|-------|-----------|-------|
| archived-to-legacy | 11 (STACK-1..11) + 5 (BASE-1..5) = 16 | Whole files/dirs removed from live source; mirror shape under legacy/. |
| edited-in-place | 26 (TOUCH-1..26) | Pre-edit byte content preserved by git history (primary) + optional pre-edit-snapshots/<file>.before.txt (secondary). NOT archived whole. |
| retained-live | 0 (EMPTY) | No live dependency on the opencode stack (D-A7 verdict). `boundary-exceptions/` directory created empty as schema future-proofing. |

## Move set (archived-to-legacy) — final 11 STACK + 5 BASE rows
[... see `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP" tables ...]

## Touch set (edited-in-place) — 26 rows
[... see `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP" Touch set table ...]

## LEAVE ALONE — final
- agents/coder/ (opencode-free by design)
- daemon/services/keyword_extraction.py + daemon/services/context_injection.py (live-shared infra; comment rephrases only)
- docs/mcp-kb-usage.md (upstream product, D6)
- .agents/*/RESULTS/ + .agents/*/LESSONS/
- data/, data_dev/ DB files (stray `opencode_sessions.db` at repo root: record-and-do-not-delete; see `evidence/phaseA/stray-db-artifacts.txt` — EMPTY in this run since no stray DB found)
- .inspiration-projects/opencode_skill_src/ (gitignored)
