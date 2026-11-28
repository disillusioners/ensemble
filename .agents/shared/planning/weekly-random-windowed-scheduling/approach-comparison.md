# Approach Comparison — recurring maintenance scheduling

Competitive fan-out, 2026-11-23. Worker `0a236c80` (DEGRADED — no trade-off skill in bank; five-axis method embedded inline), cross-checked against source audit `ac129835` + architect spot-reads @ `origin/latest` `27f9130`.

| Axis | A — Fixed cron slot | B — Pure windowed-random (chain-mode as observed) | C — Unified: recurrence + optional window (kill-switched jitter) |
|---|---|---|---|
| **Complexity** | Low — one cron string; re-arm + DST + restart-safety already shipped | High — pick + persist + re-pick + restart-safety + window edges; measured: ~1353-line seeder + knobs + re-arm seam + BR guards, and it still misleads its own listing | Medium — one path, optional block; window-edge rules remain |
| **Scalability** | Low — lands on one fixed minute | High in principle (240 candidate minutes in a 4h window) — but worthless at N=1 | Medium-high — spreads only when a window is present AND ≥2 consumers share it |
| **Maintainability** | High — universal cron, trivial debug/onboarding | Low — chain bookkeeping in config JSON; weekly intent invisible to the operator (the observed defect) | Medium — unified, but window block adds test surface |
| **Risk** | Low — predictable minute, receipt answerable at a glance | High — weekly row renders as `recurrence:"once"`; window-edge + pick/persist crash windows open; randomness spends predictability | Medium — listing can surface window + actual next-fire; window edges remain |
| **Cost** | Low — no new infra | High — two mechanisms coexist (cron + chain); guard/detector maintenance | Medium — one mechanism; modest build when gated |

## Verdicts

- **EASIER?** No. A is one string on proven machinery; B is the measured complexity bill above.
- **BETTER?** No for this consumer (N=1 weekly reclaim alone in its own 01:00–05:00 window — jitter changes which minute, not how many runs). Yes in exactly one narrow sense: load-spreading with ≥2 heavy co-windowed tasks or shared-minute external-I/O contention.
- **Ranking (this lane):** A now → C as the canonical target shape, built only when the flip condition fires. Dominant axis: **Risk** (receipt answerability), runner-up Complexity.
- **Flip assumption:** ≥2 heavy tasks sharing a window ⇒ Scalability dominates ⇒ build C per the ruling's Phase 2 spec (4.4–4.8).
