# External fact-check dispatch pattern (pre-commit deliverable verification)

2026-10-06 · review of `future-giant-plugins-reference.md` (dsh external-reference study)

What worked — reuse for future fact-check commissions:

1. **Independence protocol**: Part-A workers were FORBIDDEN from reading the deliverable; claims were restated verbatim in the dispatch prompt. Verification then cannot be laundered through the deliverable's own framing. Essential when the mission says "verify directly, not via the deliverable" (e.g. extraordinary star counts).
2. **Pin-first drift protocol**: deliverables that cite a hyper-active repo must pin a commit; workers check current HEAD against the pin (here HEAD == pin `5badb150`, zero drift) before any FAILED verdict on moved lines.
3. **/tmp-only containment**: external clones go to /tmp, never the ensemble repo. Both workers complied; no repo-workdir writes.
4. **Split verdicts allowed**: one claim (A2) was half-VERIFIED half-FAILED (release span). Forcing a single verdict would have hidden the earliest-release error. Instruct workers to verdict per sub-claim.

Skill gap: `plan-review` was a poor fit for source-verification workers (ext-meta worker rated it 3/10, applied=False). Only its read-only discipline transferred. A `claim-verification` / `evidence-review` execution skill is owed — flagged via worker skill_feedback; watch for it before the next fact-check commission.

Fidelity checks vs our own plan docs (Part-B-style) fit `plan-review` fine — the mismatch is specific to external-evidence verification.
