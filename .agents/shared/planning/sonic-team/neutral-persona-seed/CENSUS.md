# Census report — persona-neutralization sweep (neutral-persona-seed)

Base 0a9b8cd36 (v0.31.2) · branch fix/neutral-persona-seed · measured at tip a552eaf06
Census method: `git grep -nwIi` (word-boundary, case-insensitive) over the committed tree.

## 1. Per-tree file census (0a9b8cd36 -> a552eaf06)

| tree | files | +lines | -lines | hits before | hits after |
|---|---|---|---|---|---|
| daemon/ | 41 | 81 | 83 | 97 | 10 |
| tests/ | 39 | 234 | 231 | 291 | 64 |
| docs/guidelines/ | 3 | 7 | 7 | 6 | 0 |
| scripts/ | 2 | 2 | 2 | 2 | 0 |
| agents/ | 2 | 2 | 2 | 3 | 1 |
| .agents/** | 127 | 683 | 682 | 707 | 20 |
| CHANGELOG.md | 1 | 14 | 14 | 14 | 0 |

daemon/ asymmetry (+81/-83) is phase-1's seed.py rewrite, not this sweep.
agents/ count includes agents/.prompt-artifacts.sha256 (baseline refresh, see HANDOFF "What changed").

## 2. .agents/** census, per subdirectory

Pre-sweep (leader baseline, emperor|empress|empyrea): shared 111 files / 654 hits, tester 13 / 27, approver 4 / 6.
True pre-sweep scope incl. alias-example tokens: 707 lines / 130 files (shared 113/674, tester 13/27, approver 4/6).
The +20 lines / +2 files delta vs the leader's 687/128 = `constella` (21) and `my dear` (5) occurrences.

Post-sweep: shared 15 files / 19 hits · tester 1 / 1 · approver 0 / 0 — total 20, all carve-outs:
- 19 lines: actor-claim ENUM literals (pinned_by / PIN_ACTORS / Literal[...] / CHECK) in 15 ari-memoir-store docs.
- 1 line: .agents/tester/RESULTS/2026-11-05-memoir-g2-pg-corpus.md:98 (documents the lowercase enum + the refused uppercase form).
The former plan-file PATH reference class was REMOVED by the neutral filename rename: those lines now cite
`open-questions.md` and carry no persona token (see the rename commit on this branch).
Citation heuristic on the pre-sweep corpus: 193 lines carry a date/ruling/board/sha marker, 494 do not.

## 3. Out-of-scope residue (report-only, untouched)

- plugins/opendesign/** (5): vendored third-party artwork/character names + the HASHES.sha256 manifest row — leader ruling: KEEP.
- .agents/** (31): carve-outs above.
- Out-of-map persona-adjacent prose left by ruling (not gate-caught): "the sovereign ruled on many matters" (2 lines in
  tests/unit/test_memoir_constellation.py), "The Court" / "hello the court" (Discord-guild fixture names).

## 4. Alias-example parity (lockstep daemon <-> tests)

| measure | base | after | source |
|---|---|---|---|
| changed lines in the tests module | — | 227 -> 227 | leader audit + sonic-developer re-derivation |
| `stars=` occurrences | 61 | 61 | sonic-developer re-derivation |
| `add_alias` occurrences | 6 | 6 | both |
| `resolve_star` occurrences | 24 | 24 (leader metric: 18) | sonic-developer / leader |
| `upsert_star` occurrences | 37 | 37 (leader metric: 31) | sonic-developer / leader |
| neutral phrase occurrences added | — | 237 | both (leader 237 = sonic-developer 237) |
| title tokens removed | — | 233 (leader, incl. alias examples) / 217 (sonic-developer, emperor|empress|empyrea) | both |

## 5. Artifact exclusions (declared)

HANDOFF.md and CENSUS.md in this directory contain the persona token literals BY NECESSITY (they are the report
artifacts, not shipped persona surface) and are excluded from the post-sweep census.
