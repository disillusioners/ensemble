"""The wake briefing — PHASE 0 SKELETON (no behavior).

Phase 0 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase0-plan.md`` T0.2/D0.2).

PHASE 5 OWNS THE CONTENT of this module: the two-zone briefing block
(the STABLE zone — persona + keepsakes, never truncated — and the DYNAMIC
zone — recent episodes, oldest-dropped-first), the derived 24,000-char
absolute ceiling with its withhold-without-truncating backstop, and the
once-per-instance flag.

Design contract refs: ``design.md`` §8 (the injection seam — a turn-1
CONTEXT BLOCK, never a loader/system-prompt injection); ``phase5-plan.md``
(the whole phase); ``decisions.md`` DD-10/DD-11 (context block, not
system prompt; once-per-instance) and DR-1 (the token-vs-char
reconciliation); amendments AM-22 (the once-flag WRITE SITE is the
consumer, not the builder), AM-37 (the flag read is off-loop), AM-38
(every read is deadline-bounded; the build degrades rather than delaying
the turn), AM-40 (there is NO compaction-hoist membership set to add —
the hoist is truthy-keyed on any ``context_kind``), AM-43 (the render
backstop).

The service is a CONSUMER of the kill-switch: when the knob is OFF the
briefing is NOT BUILT and the memory is unreadable (the data stays —
the kill-switch is not a delete).
"""

from __future__ import annotations
