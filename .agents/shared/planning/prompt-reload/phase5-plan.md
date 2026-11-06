# Phase 5 — Docs + disclosures

## 1. Objective

Documentation and the disclosure ledger: the provider prompt-cache invalidation note (R9), the runbook, the verbatim disclosure list, CHANGELOG entry, and closeout for the review council → tester gates → staged release flow (house flow; the lane stages later, the user promotes — never promote ourselves).

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D20 | Runbook at `docs/runbooks/prompt-reload.md` (house runbook home, cf. docs/runbooks/ existing set). Contents: what the tool does; WHO can call it (ari only, three-layer gate — non-ari denial is the notify-exact `ERROR:` envelope, NOT a REFUSED code); next-turn semantics (effect lands on the NEXT dispatch — P3; tool return + the `[PROMPT-RELOADED …]` marker are the only in-turn signals; post-compaction marker erasure acceptable); provider prompt-cache invalidation as an ACCEPTED consequence (R9); the FINAL 10-code refusal table (D6/AM-6, post-architect); dev-vs-live-vs-sandbox resolution + validation rules (D2/D3: registry-first, tri-state divergence with the two fixed disclosure lines, sandbox verifies); `no_change` = file-level freshness, NOT closure currency (AM-10; multi-instance edge → `force=true`); idempotence + force semantics (incl. hash-bust `cache_busted=true` shape); kill-switch PER-CALL semantics (AM-11 — tool stays registered); **main-loop precondition: the primitive must be awaited on the daemon's main loop** (AM-13/D23); the frozen INFO-line format reference (D24/AM-15); the F7/F8 honesty notes. | One canonical operator/agent-facing home; R9 obligation discharged in both the runbook AND the tool docstring (belt and braces). | Feature essay under docs/features/ (less operational); docstring-only (loses the refusal table + resolution rules). |
| D21 | CHANGELOG entry under the live `[Unreleased]` section (house convention: dated sections below live Unreleased). One paragraph: primitive + tool + category/ceremony + docs. | House CHANGELOG convention. | No changelog entry. |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| T5.1 | Write `docs/runbooks/prompt-reload.md` (D20) incl. the R9 cache-invalidation note, the FINAL 10-code refusal table + the `ERROR:` non-ari shape (AM-6), the tri-state divergence lines (AM-5), the `no_change` file-level-freshness wording (AM-10), the marker + compaction-erasure note (AM-4), the per-call kill-switch semantics (AM-11), the main-loop precondition (AM-13), and the frozen INFO-line format (D24/AM-15). | Runbook present; contains: next-turn semantics, cache-invalidation note (verbatim phrase "invalidates provider prompt caches — accepted"), the final refusal table, resolution rules incl. sandbox-verifies, honesty notes, per-call kill-switch, main-loop precondition, INFO-line format. | docs/runbooks/prompt-reload.md (NEW) | P2/P3 (final codes) | — | AC5.1 |
| T5.2 | Disclosure ledger — ALL of plan-overview §10 (the original seven + the AM-appended five: file-side TOCTOU bounded self-heal; probe-to-compose residual; `no_change` wording = file-level freshness NOT closure currency; marker erasure by compaction acceptable; the two unverified-at-pass items routed to tester gate L1 — plus the AM-15 observability routed item) reproduced in the lane's final report AND cross-linked from the runbook. | All thirteen disclosure bullets present in the closeout; runbook cross-references the load-bearing ones. | planning dir + runbook | T5.1 | RA6 | AC5.2 |
| T5.3 | CHANGELOG entry (D21). | Entry under live `[Unreleased]`, house format. | CHANGELOG.md | T5.1 | — | AC5.3 |
| T5.4 | Closeout sweep: decisions.md final consistency pass (every D-x traceable; OQ rulings folded in when the architect answers); confirm non-goals untouched (no compaction wiring, no F7 fixes, no `current`-symlink logic changes, no UI changes). | Consistency sweep green; non-goals provably untouched (grep-level). | planning dir | T5.3 | — | AC5.4 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC5.1 | Runbook exists with all content elements: next-turn semantics + marker, R9 phrase verbatim, final 10-code table + `ERROR:` shape, tri-state divergence lines, `no_change` file-level-freshness wording, per-call kill-switch, main-loop precondition, INFO-line format, honesty notes. |
| AC5.2 | All thirteen disclosure bullets of plan-overview §10 present in the closeout; runbook cross-links them. |
| AC5.3 | CHANGELOG entry landed in the correct section. |
| AC5.4 | Consistency sweep green; scope honest (non-goals untouched). |
