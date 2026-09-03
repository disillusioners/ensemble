# Architecture Recommendation v2 — Job-Task System (Drift Lens)

**Date:** 2026-09-01 · **Relation to v1** (`architecture-recommendation.md`): verdict unchanged (WORSE on trajectory, visibility-explosion nuance); solution path §4 SUPERSEDED by this document + `drift-history-and-constitution.md`. New inputs: deep history (to 03-15), 22-writer census, constitution draft, governance design.

## What changed from v1

1. **Counts sharpened:** "6+ uncoordinated transition authorities" → **22 writers, 9 uncoordinated, 8 bypassing `validate_transition`, 5 bypassing every guard**. The state machine is advisory outside the W1 happy path; W5 writes an illegal `paused→done` (documented "legacy drift").
2. **Timing corrected:** receipts began **05-24** (not 07-03); proxy doctrine explicit **06-28** (half-landed); JobItem **not born** a proxy. The user's wave-1 framing was right in mechanism, late in dating.
3. **Ordering tightened:** **C strictly AFTER B** — constitutional rule, not preference (C-without-B makes divergence invisible).
4. **Governance added:** Phase 0 census tests (immediate, ~½ day, pure add) through Phase 5 FK; D gated on subordinate-count >4 or family regrowth. Registry source-of-truth = code constants; doc asserted equal by bidirectional AST drift tests (tool-name precedent).
5. **The line, operationalized:** D1–D4 booleans (writer registry / event-time terminal / one-answer / fail-closed handles). All four were red by 07-03 — two months before anyone felt it. "How much drift" is not an amount; it is four failing checks.

## Updated path (summary)

**A → B → C** (structural, v1 core unchanged) **+ governance phases 0–5** (see `drift-history-and-constitution.md` §4 for the full table) **+ D deferred-with-trigger.** Phase 0 ships first and independently: static sets + census tests + pack — no behavior change, immediate drift visibility, and the machine-check that would have caught 05-24, 07-03, and 08-30 at landing.

## Anti-recommendations

No reverting receipts (49% of rows, load-bearing) · no re-litigating JAFP (held, −1004 lines) · no kill-switch-as-governance · no seam-freezing · no big-bang D.
