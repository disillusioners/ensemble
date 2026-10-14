# Deep Review — Mission M1 correctness gate (feature/mission-class @ 2490e37f)

Date: 2026-09-02 · Mode: Deep-Review (council) · Governor: 2c9ca609 · Councilors: `agentic` 1a050bb8, `coding` c2130bb3 · Skill: code-review

## Verdict: SHIP (conditional) — 0 🔴, 8 🟡, ~5 🟢

| Boolean | Result |
|---|---|
| projection-sound | Y (mechanics; council split on classification — §8.3 drift shipped as finding 1) |
| OFF-path-inert | Y (config/yaml diff vs e676ddea empty; key-set/order identical, dual-verified; OpenAPI schema additive even when OFF) |
| purity-real | Y (zero DML in diff, dual-verified; census 23/6 static + drift tests 10/10 green) |
| amendment-faithful | **N** — §8.3 contradicts shipped code+tests (epoch None-when-terminal vs constant 1; terminal_reason gated vs unconditional) |
| census-23 | Y (dual-method) |

## Key facts
- F2 claim (OFF-path byte-identity) TRUE; F5 claim (test blind spots closed) OVERSTATED — ON-path tests real, but zero SQLAlchemyError-injection coverage.
- W4 ordering correct: mission_resolver.py:547-558, work_resolver.py:1376-1380.
- ON-path N+1: per-row JobItem SELECTs (mission_resolver.py:600-650 → work_resolver.py:1526-1543) — owed before kill-switch flip, not before merge.
- Kill-switch = `ENSEMBLE_MISSION_PROJECTION_ENABLED`, env-only, default OFF, restart-to-flip; lazy first-access resolution (not boot-time) — minor contract nuance.
- Pre-existing (not branch-attributable): runtime revives TERMINATED too (instance_messaging.py:1788-1808) vs spec "cancelled true-terminal" — M2+ ticket.

## Conditions (ordered)
1. Before merge: findings 1+2 — ~10-line §8.3 doc edit + docstring fixes (docs/job-task-system.md:1087-1089, :402-403, :1072-1075; mission_resolver.py:74, :358).
2. Before ON-flip: JobItem batching (finding 3) + error-path tests (finding 4).
3. M2+ ledger: findings 6, 7, 8 + hygiene 🟢s.

## Lesson
Spec placeholder semantics ("epoch constant 1 until M4-ii is best-effort") must be transcribed into the ratified contract verbatim — the ADR §8.3 row was written from the M4 end-state, not the M1 placeholder, creating code/doc/test three-way drift caught only by cross-checking all three.
