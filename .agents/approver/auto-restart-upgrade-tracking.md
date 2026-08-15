# Tracking: auto-restart-upgrade

## Iteration 001 — 2026-08-15T21:40Z
- Worker: approve-worker-plan (795bfed9-8932-4872-bd81-58ac7a7a7f0a), skill: plan-approval
- Verdict: APPROVED
- Blocking issues: none
- Notes (7): launcher exit-code enforcement on launchd (§4.1); pre-Phase-5 schema-check definition (§4.2); manual rollback vs manifest gate (§4.3/§5); third port PROD_PORT 9797 omitted (§4.1/OQ1); promote restart mechanics unspecified (§5.4); gate-vs-drain marker ordering implicit (Phase 4+); label hygiene (m1–m8/m2, R9 order)
- Unverified: original hard-req #1–#7 source text; prod install-dir state; council process metadata; effort estimate
- Worker independently re-verified C1–C4, C7/C8 seams, reuse predicates against codebase — all confirmed
