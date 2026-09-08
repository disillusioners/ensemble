# FE spec-mirror fix blindness — F-5 incident (2026-09-08, job-queue-mission-tree)

**Pattern**: This repo's FE test convention is plain-TS **logic-mirror specs** (no Angular TestBed — see frontend blueprint). A bug fix implemented ONLY inside the spec's mirror harness passes the entire jest suite (299/299) while the production component stays broken.

**Incident**: F-5 fix commit `906e51bf` wrote `liveMissionsPayload.set(liveMissions)` into `job-queue-indicator.component.spec.ts:447` (mirror) — with a comment describing the exact production behavior — but never into `job-queue-indicator.component.ts` `applyFetchResults`. Result: pill count correct (count-only write), tooltip breakdown eternally `(0,0,0)`, LIVE MISSIONS panel section never renders. Caught ONLY by live-stack Playwright (real component), not by jest, not by tsc.

**Detection rules for future FE fix gates**:
1. When a fix commit touches BOTH `*.component.ts` AND `*.spec.ts`, diff-check that every behavioral line added to the mirror has a counterpart in the production file (grep the symbol, not the phrasing).
2. A spec comment that describes production behavior ("so X can read Y") is a strong smell the production edit was intended but dropped.
3. Jest-green + tsc-green is NOT behavioral proof for component fixes — one live-stack Playwright pass against the real component is the minimum bar for UI-facing acceptance criteria.

**Related**: FE blueprint "Template-extraction audit rule" (DOM bindings invisible to plain-TS specs) — same blindness class, template side.
