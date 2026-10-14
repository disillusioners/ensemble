# 2026-09-27 — Maintenance Console Phase 1 Deep-Review (feature/maintenance-console @ 3fb798d1)

Verdict: NEEDS_CHANGES — 1 blocking critical. Governor `3cd1c6e7-5c03-4b17-ae25-05bd67797db7`, 2 councilors (agentic + coding), converged 2/2 after 1 refinement round.

## Lessons worth keeping

1. **"Documented/accepted" severity downgrades must be verified against the docs, not the reviewer's summary.** Round-0 split (coding: APPROVED, agentic: NEEDS_CHANGES) collapsed in Round 1 by asking one factual question: *is Host-spoofability (DNS rebinding) explicitly accepted anywhere?* Grep across all 6 plan docs + guard module: zero hits. The acceptance record covers no-auth/localhost/XFF/bind-deferral — never client-controlled-Host. Adjudication pattern: convert a severity disagreement into a documentable factual question, send it back to BOTH councilors, converge on the evidence.
2. **Dev-internal "9/9 PASS / SHIP" reviews can be accurate on every claim and still miss blockers** — C1, W1, W2 were all outside its claim set. Trust-but-verify must check claim *coverage*, not just claim *truth*.
3. **File-path drift in dispatch context**: guard lives at `daemon/routers/maintenance_origin_guard.py`, NOT `daemon/services/` as the task context said. Always let councilors correct paths; don't anchor on the dispatch listing.
4. Council canonical-dedup can cap at 2 models (agentic + coding) even when max_councilors=4 — 2/2 convergence with evidence-dense reports is still a valid deep review; no degraded-confidence flag needed at ≥2 distinct models.


## Re-verify outcome (same day, fix pass 0bcbfc37, HEAD 009ff933)

**C1 VERIFIED, L2–L7 no regression, fix-pass APPROVED.** Guard gate at `maintenance_origin_guard.py:303-306`; rules 3/4 Host-blind, rule 1 untouched (C2 stands, ruling recorded decision-log.md:46-49 v1-scoped), INV-10 order byte-stable vs baseline; cells run on real disposable PG (PG_TEST_* conftest contract): 12/12 allowlist unit + 13/13 origin-guard integration + HTTP precedence pin green — the latter also settled cycle-1's "case 28" discrepancy (the pin now genuinely exists, `test_maintenance_checkpoint_cleanup_api.py:1249-1269`).

Lessons:
1. **Fix bundles outrun their tickets** — the "C1 fix commit" range carried 8 commits touching L3 (lock-release refactor), L6 (migration SQL), and lifespan. Always scope the re-verify diff, not the fix description; the regression map was load-bearing.
2. **Claim-count precision**: dev's "29/29 unit" figure mapped to no single cell grouping (class=12, file=54, integration=13). Green is green, but numeric claims from dev-internal reviews should be re-based before being quoted forward.
3. My own dispatch misattributed the amendment host file (phase1-backend.md → actually plan-overview.md); worker caught it via empty-diff. Dispatch prompts should let the diff stat name the file.
4. Residual static-only bypass classes (IPv4-mapped IPv6, 0.0.0.0, prefix, ws/wss) are defended by construction — exact-match frozenset + isinstance(IPv4Address) — but unpinned; 🟢 regression pins recommended.
