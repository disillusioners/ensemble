# Approach Comparison: Highest-Leverage Structural Change

Companion to `architecture-recommendation.md`. Five-axis aggregation by the architect over W4's structural analysis, cross-checked against W1 (seam map), W2 (frequency), W3 (trajectory).

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: Fail-closed linkage** (require explicit `work_id` on job-driven dispatch; demote auto-mint to error) | Low add (~1 day) | Neutral | High gain — freezes sweep-family regrowth at the root | Very low blast radius; closes B's phantom-handle window | ~1 day | **Adopt — same-PR prerequisite for B** |
| **B: Inline mirror transition + liveness-gated sweep predicate** (task_processor on_success idempotent UPDATE; replace `:2284` blanket skip) | Medium (2–4 days, one write site, proven command shape) | Removes the 7 h latency class; event-driven vs polling | High gain — retires f2's mirror slice + zombie class | 🟡 non-atomic with `complete_task` (bounded crash window; f2 survives as bounded backstop); needs A first | 2–4 days | **Adopt — the core of the pair** |
| **C: Read-model truth** (terminal rows consult instance liveness; mission/mirror split; raw admission always visible) | Low-Med (1–2 days read-side + possible FE) | Neutral | High gain — retires the alarm churn (the dominant measured pain) | 🔴 **alone it masks drift** (zombies + lag become invisible); must pair with B | 1–2 days | **Adopt — paired with B, never alone; ship no later than B** |
| **D: Ground-truth linkage + unified reconciler** (absorb f1/f2/reaper) | High (schema migration + f-family rewrite + kill-switch revalidation) | Best end-state | Best end-state (family collapses to one reconciler) | Lands on the most incident-dense surface while family is starving (flat 1–3/day, 0 on 09-01) — regression risk exceeds option value now | High | **Defer, not reject** — revisit if the family re-grows after B+C |

**Winner: the minimal pair A→B→C** (A same-PR-first; C parallel-landable but no later than B). Dominant axes: Maintainability (only B+C both fix and retire) and Risk (A≈0 < C < B ≪ D).

**Leader's original candidate (reconciliation seam + inline transition):** partially already built (both write directions exist — `reconcile_terminal_task` job_feedback_observer.py:3615-3642; instance-terminal cascade instance_lifecycle.py:3978-3993). The candidate's *inline transition* half survives as B; its *seam* half is refined into "single transition authority + cross-checking read model" rather than new reconciliation writes.

**Ordering hazards (W4):** B-before-A leaves the phantom-`work_id` no-op window open; C-before-B hides the lag signal; D-after-B+C only if frequency data shows the family re-growing. DLQ-replay (DEAD→QUEUED, job_state_machine.py:60) × instance-revive (instance_messaging.py:1486-1510) can legally produce "revived instance under DEAD job" — C's renderer must not let instance liveness override DEAD for mission rows.
