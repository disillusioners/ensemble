# LCA Stage-2: fused-judge truncation-before-parse discards correct verdicts (MERGE-BLOCKING, confirmed)

Date: 2026-09-16
Gate: LCA resolver Stage 2 (THE FLIP) final merge gate — delta 0ea60d91..f926de24, branch feature/lca-resolver-stage2
Found by: Job 8 live-LLM probe (instance f8f5397f) + deterministic verification (instance c9a80d47, commit 20024fda)

## Defect

`JUDGE_MAX_OUTPUT_CHARS: int = 400` (daemon/services/attestation_report_judge.py:120) is a **hardcoded module constant — no env/config knob** — applied **truncate-BEFORE-parse** at 4 sites (legacy 893-899/938-941; fused 1315-1318/1353-1356). The Stage-2 delta (07e9e8cc) made `judge_fused_bundle_async` authoritative (graph.py:5306) while unconditionally reusing the shared cap. The fused system prompt (lines 1059-1079) MANDATES a payload of 5×120-char evidence_cited + 240 advisory + 240 rationale ≈ >400 chars **by construction** — a compliant model response can never parse. Truncated fragment → invalid JSON → `_parse_fused_judge_response` None → retry → same truncation → `verdict="unparsable"` (attempt=2).

## Attribution

Cap+order PRE-EXISTING on the legacy window-judge (7a899517; its expected `{is_complete_report, reason}` shape ≈80-150 chars fits). **INTRODUCED-BY-DELTA on the fused AUTHORITATIVE path** — the legacy prompt's shape fits the cap; the fused prompt's mandated shape never does.

## Blast radius (under MODE=enforce, `quick` tier)

- Every verbose-but-correct fused verdict silently downgrades to unparsable.
- Rescue path (graph.py:5375-5399) UNREACHABLE — genuine reports (98b59dd7 shape) false-deny+nudge, ride to DEFAULT_DENY_BOUND (4 cycles: bound semantics d+1>b) → terminal_after_bound forced allow.
- Deny-band else-branch graph.py:5400-5406 conservatively denies (path-(d)-exact) — safe direction, wrong reason.
- MODE=dry UNAFFECTED (judge never fires outside enforce).
- Judge kill-switch=0 is NOT a workaround (kills rescue entirely, worsens marker/A bands).
- Budget sentinel intact: 2 attempts/eval, ≤1 logical invocation.

## Evidence

- Live probe: BOTH payloads (genuine report 2434 chars; child-lie 1614 chars) contained correct `"verdict"` in raw output (quoted in first_unparsable_excerpt) yet returned unparsable. Latency 39.8s / 62.6s.
- Deterministic repro (no credentials): tests/probe/lca2_judge_truncation_repro.py @ 20024fda — compact 122-char JSON → verdict complete; compliant 997-char JSON → truncated to exactly 400 → unparsable×2.

## Why prior gates missed it

All prior gates (developer matrix 901/1, adversarial, council) used STUBBED judges with short canned responses that fit under the cap. Only a LIVE-LLM probe exposes prompt-shape × cap interactions. **Lesson: any gate over an LLM-parsing seam needs at least one live (or realistically-shaped >cap canned) probe of the full mandated response shape.**

## Recommended fix (NOT applied — no production changes in gate)

Introduce `FUSED_JUDGE_MAX_OUTPUT_CHARS = 2048` (or parse-then-truncate-excerpt) used at the two fused sites only; legacy keeps 400. Conservative fail-safe (unparsable×2 → deny+nudge) preserved either way.
