# Phase 5 — Docs + disclosures

## 1. Objective

Documentation and the disclosure ledger: the provider prompt-cache invalidation note (R9), the runbook, the verbatim disclosure list, CHANGELOG entry, and closeout for the review council → tester gates → staged release flow (house flow; the lane stages later, the user promotes — never promote ourselves).

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D20 | Runbook at `docs/runbooks/prompt-reload.md` (house runbook home, cf. docs/runbooks/ existing set). Contents: what the tool does; WHO can call it (ari only, three-layer gate); next-turn semantics (effect lands on the NEXT dispatch — P3; tool return is the only in-turn signal); provider prompt-cache invalidation as an ACCEPTED consequence (R9); the refusal-code table (D6 final set); dev-vs-live resolution + validation rules (D2/D3); idempotence + force semantics; the F7/F8 honesty notes; kill-switch. | One canonical operator/agent-facing home; R9 obligation discharged in both the runbook AND the tool docstring (belt and braces). | Feature essay under docs/features/ (less operational); docstring-only (loses the refusal table + resolution rules). |
| D21 | CHANGELOG entry under the live `[Unreleased]` section (house convention: dated sections below live Unreleased). One paragraph: primitive + tool + category/ceremony + docs. | House CHANGELOG convention. | No changelog entry. |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| T5.1 | Write `docs/runbooks/prompt-reload.md` (D20) incl. the R9 cache-invalidation note and the final refusal table (post-architect D6 ruling). | Runbook present; contains: next-turn semantics, cache-invalidation note (verbatim phrase "invalidates provider prompt caches — accepted"), refusal table, resolution rules, honesty notes, kill-switch. | docs/runbooks/prompt-reload.md (NEW) | P2/P3 (final codes) | — | AC5.1 |
| T5.2 | Disclosure ledger — the §10 list of plan-overview.md reproduced in the lane's final report AND cross-linked from the runbook (F7i, F7ii, F8, next-turn semantics, cache invalidation, mid-turn eviction semantics, P1 divergent-node-set hazard). | All seven disclosures present verbatim in the closeout; runbook cross-references the load-bearing ones. | planning dir + runbook | T5.1 | RA6 | AC5.2 |
| T5.3 | CHANGELOG entry (D21). | Entry under live `[Unreleased]`, house format. | CHANGELOG.md | T5.1 | — | AC5.3 |
| T5.4 | Closeout sweep: decisions.md final consistency pass (every D-x traceable; OQ rulings folded in when the architect answers); confirm non-goals untouched (no compaction wiring, no F7 fixes, no `current`-symlink logic changes, no UI changes). | Consistency sweep green; non-goals provably untouched (grep-level). | planning dir | T5.3 | — | AC5.4 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC5.1 | Runbook exists with all eight content elements; R9 phrase present verbatim. |
| AC5.2 | All seven disclosures present in the closeout; runbook cross-links them. |
| AC5.3 | CHANGELOG entry landed in the correct section. |
| AC5.4 | Consistency sweep green; scope honest (non-goals untouched). |
