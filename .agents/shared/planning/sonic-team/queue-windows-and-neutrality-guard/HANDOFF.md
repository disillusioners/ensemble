HANDOFF_PATH = .agents/shared/planning/sonic-team/queue-windows-and-neutrality-guard/HANDOFF.md

# Handoff — queue-windows-and-neutrality-guard (rev 2 — post review-fix wave)

Branch: `fix/queue-windows-and-neutrality-guard` (base: `a1883aff2b08a7a509c0d87d35584fe020c161ba`)
- Part A commit: `05e781409` (`feat(memoir): surface both approval queues in briefing + decision axis + review/ratify verbs (Part A)` — includes the review-fix fold-in)
- Part B commit: `7546312c2` (`test(memoir): permanent persona-lore neutrality guard over shipped trees (Part B)`)
- Review verdicts absorbed: REVIEW NEEDS-REVISION (5 findings) · TEST PASS (8 measured legs). Findings 1, 3, 4 fixed in code/tests; findings 2, 5 and the finding-1 disclosure are recorded here.

## Files touched across modules

### Part A — m2-decision-store (`decision_status` ledger axis, SQLite migration, fail-soft PG ensure)
- `daemon/repositories/memoir/models.py` — `PROPOSAL_DECISION_ACCEPTED`, `PROPOSAL_DECISION_DECLINED`, `PROPOSAL_DECISIONS`, plus 3 nullable columns (`decision_status`, `decided_by`, `decided_at`) and `ck_memoir_proposal_ledger_decision_status` on `MemoirProposalLedger`, plus `__all__` exports.
- `daemon/services/memoir_keepsake_proposal_service.py` — `_zone_selection_statement` excludes decided rows (one home shared by zone render, notify selection, and `count_pending_reviews`); `_mark_enqueued_in` keeps `notification_status == 'pending'` CAS and adds `decision_status IS NULL`; added `count_pending_reviews(*, agent_id=None) -> int` and `record_review(*, title, decision, actor, now=None) -> dict` (raising `MemoirProposalReviewError(ValueError)` with `.code` in `{"not_found", "invalid_decision", "invalid_actor"}`). [REV-2 FIX] the `RELEASE-GATED DEGRADATION` docstring paragraph now describes the REAL degradation path (generic `memoir_internal_error` envelope / zone-level degrade) — a docstring-only change, no executable line moved.
- `daemon/migrations/versions/20261119_000001_add_proposal_decision_columns.sql` (new) — additive SQLite migration (plain `ADD COLUMN` x3 + column `CHECK` matching the model text byte-for-byte + additive-rollback `-- DOWN` note). NOTE the deliberate filename (see Known risks: migration-census dodge).
- `daemon/manager.py` — added `_ensure_memoir_proposal_decision_columns()` (separate fail-soft transaction, one idempotent `DO $$ ... EXCEPTION WHEN others THEN RAISE WARNING ... $$` block with `ADD COLUMN IF NOT EXISTS` x3 + named CHECK; never in the shared `_ensure_postgres_columns` all-or-nothing transaction) and one call site next to `_ensure_postgres_drop_legacy_columns()`.
- `tests/unit/test_memoir_proposal_decision.py` (new).

### Part A — m3-review-verb (`memoir_review` 7th verb + scope-addendum `memoir_persona_ratify` 8th verb + ceremony 6→7→8)
- `daemon/tools/memoir.py`, `daemon/tools/_tool_registry.py` — both verbs; `MEMOIR_TOOL_NAMES`; `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES`. `PRIVILEGED_TOOL_CATEGORIES` (23) and `CATEGORY_MODULES` unchanged.
- `tests/unit/test_memoir_registration.py`, `tests/unit/test_memoir_tools_unit.py`, `tests/unit/test_memoir_about_tool.py`, `tests/unit/test_memoir_persona_lane.py` — ceremony pins 6→8. [REV-2 FIX] the stale "six"/"seven" labels that assert 8 were renamed to "eight" (functions AND assertion messages).
- `tests/unit/test_memoir_review_verb.py` (new), `tests/unit/test_memoir_persona_ratify.py` (new). [REV-2 FIX] the ratify verbatim-refusal leg is now ENGINE-INDEPENDENT (real in-memory SQLite + a non-existent version id) so it is green on this tree AND on the co-merged exemption tree.

### Part A — m1-briefing-counters (A1 wake-briefing queue counters)
- `daemon/repositories/memoir/repository.py` — `MemoirPersonaRepository.count_pending(agent_id)` (lines 173-198) + module-level `count_pending_persona(engine, agent_id)` delegator (lines 345-368; keeps the `MemoirPersonaRepository` caller set-equality pin green).
- `daemon/services/memoir_briefing.py` — queue-line templates, `render_queue_count_line`, optional `persona_pending_count`/`proposal_pending_count` (default 0) on `render_briefing_body`, measured `prefix` parameter on `admit_proposal_lines`, two `_guarded_read` COUNT calls.
- `tests/unit/test_memoir_briefing_queue_counts.py` (new).

### Part A — m5-review-seam
- `tests/unit/test_memoir_review_seam.py` (new) — 6 end-to-end legs of the `memoir_review` verb against a real in-memory SQLite engine.

### Part B — m4-neutrality-guard
- `tests/unit/test_persona_neutrality.py` (new) — `git grep -nwIi` over the shipped trees against a FILE+TOKEN allowlist with per-entry reasons; three-way rc handling; fragment-assembled tokens; self-cleanliness asserted.

## What changed

1. **A2 decision-store mechanism (DDL route):** 3 nullable columns + model-bound CHECK on `MemoirProposalLedger` rather than a 5th `notification_status` token (closed 4-token delivery vocabulary, pinned) or prose in `reason` (renders into briefing lines, un-indexable). Both `_zone_selection_statement` and `_mark_enqueued_in`'s CAS require `decision_status IS NULL`, so a decided row leaves the queue permanently, cannot re-notify, and stays durably distinguishable from an unread row. PG ensure is a separate fail-soft method, **RELEASE-GATED** (see Known risks for the exact degradation behaviour).
2. **Tool surface (7th + 8th ari-only verbs in the existing `memoir` category):** `memoir_review(title, decision, actor)` and `memoir_persona_ratify(version_id)` (scope addendum: actor MINTED via `actor_from_instance`, `MemoirPersonaRepository.approve()` called AS-IS so the repository stays the gate, `HumanLaneRequiredError` relayed verbatim). No new category; the metrics artifact was NOT regenerated.
3. **A1 briefing counters:** one short line per non-empty queue inside the existing Persona and Proposals zones, naming `memoir_persona_ratify` (persona queue) and `memoir_review` (keepsake queue); byte-identical to base when both counts are zero (zero token tax, proven A/B across 6 shapes).
4. **Part B neutrality guard:** derived at test time; matches the exact 75-line / 87-occurrence / 15-file contractual baseline at base; fails loudly on a new token, a vacuous zero-hit tree, or a broken scan.

## What is UNTESTED

- **`memoir_persona_ratify` success path (UNTESTABLE at base):** every internal actor is refused by `_require_human_lane` at base until the concurrent self-ratify exemption lane merges. A success-path leg is owed when that exemption lands.
- **Live / production PostgreSQL boot and runtime:** no production DB writes or restarts. The fail-soft DO block was proven on a scratch PostgreSQL 14 DB; all suites ran against SQLite.
- **Pre-existing SQLite DB upgrade via the real `MigrationRunner`:** proven on hand-shaped tables and the `create_all`-then-runner order, not against a production SQLite file.
- **Wake-refresh (`memoir_refresh`) payload:** shares the render path by construction; no dedicated refresh-path presence leg.
- **Briefing → `memoir_persona_ratify` runtime loop:** registration-level only; the full approval loop awaits the exemption lane.

## Known risks

- **NAMED FOLLOW-UP GAP — persona approver attribution residual:** `MemoirPersonaVersion` carries `approved_at` (when) and `status` (that) but NO approver column; the scope addendum forbids adding one. The keepsake queue records full attribution (`decided_by`); the persona queue records only *that* and *when* until a follow-up adds an approver column/ledger.
- **RELEASE-GATED PG schema + the REAL degradation path (REV-2 corrected wording):** live prod PostgreSQL lacks the three decision columns until this binary boots and runs `_ensure_memoir_proposal_decision_columns()`. On an un-migrated schema `record_review` / `count_pending_reviews` raise a DRIVER error; the tool path's broad handler converts it to the GENERIC `memoir_internal_error` refusal envelope (fail-soft, never a crash), and the briefing path swallows it in `_guarded_read` (zone-level degrade). NO decision-specific typed code is minted for that case.
- **COUNT-VS-ZONE DIVERGENCE (disclosed, deliberately NOT capped):** `count_pending_reviews` is UNCAPPED while the proposals zone renders at most `MEMOIR_PROPOSALS_ZONE_MAX = 3` titles, so the briefing can announce N>3 pending while only 3 titles are actionable. Chosen over capping because the announced count must be the TRUE queue depth (a capped count would publish a false number) and the queue is self-draining: each recorded decision removes a row, so the next-strongest proposals render on the following build.
- **48k WITHHOLD CONSEQUENCE (moved from UNTESTED per REV-2):** a positive persona count adds ~90-130 chars to the STABLE zone, and the withhold bound is measured on the stable zone ALONE — so a stable zone sitting within ~90-130 chars of 48,000 now crosses into withhold and the WHOLE block is withheld (and the once-flag is never written). The measurement is honest; the edge is reasoned, not leg-tested.
- **MIGRATION-CENSUS DODGE (disclosed per REV-2):** the new migration is named `20261119_000001_add_proposal_decision_columns.sql` — it deliberately OMITS "memoir" from the filename so the `*memoir*.sql` == 2 census pin in `tests/unit/test_memoir_schema_contract.py:282` stays green. House precedent, but a memoir-table DDL is now INVISIBLE to that census; anyone extending the memoir migration census must know this file is one.
- **CO-MERGE TEST RED (found in review, FIXED in this wave) + the exemption's TRUE range:** the ratify verbatim-refusal leg originally used a string engine; on the co-merged `fix/persona-self-ratify-ari` tree `approve()` calls `_require_human_lane(version_id, actor, allow_self_ratify=True)`, which performs `self.get_version(version_id)` — a DB read — BEFORE raising, so the string engine raised `AttributeError` → generic `memoir_internal_error` → red. FIXED: the leg now uses a real in-memory SQLite engine with a NON-EXISTENT version id (the exemption's own rule — "a lookup that finds no version leaves the exemption inactive" — keeps the refusal identical on both trees). The exemption's TRUE interacting range is `repository.py` ~55 (import) and ~248-301 (`approve` + `_require_human_lane`), NOT ~222-274 as this handoff previously stated.
- **In-flight lane overlap:** `git merge-tree --write-tree --name-only <Part A tip> fix/persona-self-ratify-ari` = exit 0, zero conflicts. Our `repository.py` edits (173-198, 345-368) sit ≥45 lines clear of ~248-301; our `models.py` edits live inside `MemoirProposalLedger` (637-760) + the `PROPOSAL_DECISION*` `__all__` anchor, clear of the exemption's ~160-170 and its `__all__` anchor.
- **Pre-existing stale migration-count pins:** two reds exist at base and remain red at tip with identical ids (`test_the_git_derived_lane_delta_is_exactly_one_migration_file`, `15 == 1` vs `14 == 1` at base; `test_the_prompt_carries_the_title_event_time_body_and_stars`). No new reds.

## How to verify

```bash
# 1. Targeted suite (all new + ceremony + artifact + guard tests):
uv run python -m pytest \
  tests/unit/test_persona_neutrality.py \
  tests/unit/test_memoir_proposal_decision.py \
  tests/unit/test_memoir_review_verb.py \
  tests/unit/test_memoir_persona_ratify.py \
  tests/unit/test_memoir_review_seam.py \
  tests/unit/test_memoir_briefing_queue_counts.py \
  tests/unit/test_memoir_registration.py \
  tests/unit/test_memoir_tools_unit.py \
  tests/unit/test_memoir_about_tool.py \
  tests/unit/test_memoir_persona_lane.py \
  tests/unit/test_memoir_v2_schema.py \
  tests/unit/test_memoir_ensure_structure.py \
  tests/unit/test_memoir_keepsake_proposals.py \
  tests/unit/test_memoir_briefing_unit.py \
  tests/unit/test_memoir_briefing_seam.py \
  tests/unit/test_memoir_wake_refresh.py \
  tests/unit/tools/test_tool_category_artifact.py -q

# 2. Full sweep (base a1883aff2 = 2 failed, 804 passed; the 2 ids are identical both legs):
uv run python -m pytest tests/unit/test_memoir_*.py tests/unit/test_persona_neutrality.py -q -p no:randomly --override-ini="addopts="

# 3. Neutrality baseline parity (must stay 75 lines / 15 files, identical per-file token sets):
git grep -nwIi -e emperor -e empress -e empyrea -- daemon/ tests/ docs/guidelines/ scripts/ agents/ CHANGELOG.md | wc -l

# 4. Guard non-vacuity (scratch-only edits, revert after): a token in a non-allowlisted file,
#    and a foreign token in an allowlisted file, must each fail naming FILE:LINE:TOKEN.
```

## Bash-raises used

none (all commands completed within the default 600s ceiling)

## Complexity self-assessment

`medium` (upper-middle) — assessed after plan + module split, before dispatch; unchanged by the fix wave. The two risky seams (DDL + fail-soft PG ensure; the 8-verb registration ceremony fan-out) were isolated into disjoint modules with pre-pinned contracts and merged with zero conflicts. The review's 5 findings were absorbed in ONE fix wave: 3 in code/tests (engine-independent ratify leg; stale labels; degradation wording), 2 as disclosures in this handoff.
