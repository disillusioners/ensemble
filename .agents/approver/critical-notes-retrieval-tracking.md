# Tracking: Critical-Notes Tiered Loading & Maintenance (slug: critical-notes-retrieval)

Plan: 3-file package — architecture-recommendation.md (209) + decisions.md (56) + approach-comparison.md (70); branch feature/critical-notes-retrieval.

## Iteration 001 — REJECTED (2026-09-15, worker ffab9dc2-8a40-47b9-afc5-d43a9fd9ba23, skill plan-approval)

Worker verdict: REJECTED (2 blocking rows, same root cause — merged to 1 by approver dedup).

### Blocking (must fix before re-submission)
1. **D3/§4.5 prompt-section reference form contradicts Convention v2.** decisions.md D3 (lines 26-27) + architecture-recommendation.md §4.5 (line 98) spec "section refs as `file.md → Section Name`" and attribute that form to docs/agent-prompt-writing-guide.md — but the guide's Convention v2 (lines 95-108) mandates `See <Section Name>` (same-agent) / `See <agent>'s <Section Name>` (cross-agent) and forbids BOTH bare file refs and the arrow form; tests/unit/tools/test_prompt_section_reference_integrity.py enforces repo-wide. As written: follow plan -> integrity gate fails; follow guide -> implementation drifts from plan spec. Fix: replace both clauses with the Convention v2 forms.

### Notes (non-blocking, carried forward)
- N4: §4.3 prose reads as if `_stable_id_for("project")` form is the work; actual work is call-site plumbing of instance_id into build_project_context_message (context_messages.py:146-211, line 1599; plan §9 #5 already flags). Tighten wording.
- Otherwise strong: 20+ file/line/function claims verified against source (all matched); migration dual-driver strategy correct; D4 no-new-env-flag rollback posture consistent with repo convention; §4.6 risk ladder severity-ordered; v2 self-corrections (R25 wording, C2 semaphore) verified.

Next: awaiting revised plan → iteration 002.

## Iteration 002 — APPROVED (2026-09-15, worker 7f862df3-332a-4042-8a43-aa1a885199d4, skill plan-approval)

Worker verdict: APPROVED (0 blocking; 7 notes).

Prior-blocker check (approver, post-verdict): iteration-001 blocker (D3/§4.5 Convention v2 section-ref form) independently re-verified as fixed by the fresh worker — D3 now states `See <Section Name>` forms per docs/agent-prompt-writing-guide.md:91-108. Carried note N4 (§4.3 `_stable_id_for` call-site plumbing wording) persists as citation-drift note N2, still non-blocking.

### Notes carried to implementation (worker N1-N7)
- N1: stale citation `config.py:2237-2239` for `_bm25_score` → actual skill_search_service.py:182 (BlueprintMatcher blueprint_matcher.py:118).
- N2: stale citation `context_messages.py:146-211` for build_project_context_message → actual :532-593 (helper `_stable_id_for` :146-225); call site :1599 correct.
- N3: `critical_notes.tiered` knob semantics unclear vs D4 always-on — drop or document what it gates.
- N4: Migration-2 precondition mechanism — add column to SchemaMigration model or inline check; pin choice in migration file-format docs.
- N5: §7 Phase-1 budget math (35k chars) correct but derivation unstated.
- N6: `project_cn_remove` cascade=True behavior — document exact cascade semantics (mirror R20 reject-don't-evict).
- N7: one-line confirmation that reserved Phase-3 knobs get no ENSEMBLE_* env var (D4 compliance).

Final: APPROVED — plan cleared for implementation. Tracking closed (file preserved as historical record).
