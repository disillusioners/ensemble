# Phase C Round-2 C.4 Audit — worker#2's recovery edits vs plan spec

Auditor: Phase C executor #3 (round-2 recovery), 2026-10-13.
Subject: the 43 tracked-modified C.4 files left by worker#2 (produced 11:54–13:15 per
the hang-recovery brief; gate run killed by dispatcher telemetry error, edits unverified-
but-supervised). Two additional tracked-modified files are FOREIGN runtime writes and are
EXCLUDED from C.4: `.agents/shared/planning/designer-agent/install-audit.jsonl`,
`.agents/tidier/notes.md` (preserve, never commit, never destroy).

Method: file-by-file `git diff` review against phaseC-opencode-excision.md §13.5 TOUCH
set + §13.7 C.4 + §14 folds. N-3 honored: plan line-cites are illustrative; exec-time
grep is truth. SEMANTICS verified, not just presence.

## R1 validation summary (S1)

| Gate | Decision | Validation performed by auditor |
|------|----------|--------------------------------|
| Step 1.5 BLOCKING GATE (5-pair diff) | PASS ×5 | Re-run by auditor at current tree: all 5 pairs byte-identical; `agents/leader[v2]` absent (R7). Matches dispatcher re-run (PASS ×5) and worker#2's 11:53Z record. |
| C.1 boot evidence | ADOPT (validated) | C1-boot.log 35847B: "Development Mode", engine `127.0.0.1:5432/ensemble_dev`, v0.20.1 banner, 0 opencode lines, "Graceful shutdown complete". livez/readyz JSONs 200 (checked_at 10:32:37 inside boot window). Commit 2e3bf2b2a @10:33:18 AFTER boot 10:32:05–48 (correct order). Checkpointer log line `localhost:5432/ensemble` is a DISPLAY quirk (daemon/persistence.py:222-224 logs raw file-config values; actual DSN is env-resolved `ensemble_dev` via _build_pg_connection_string) — not a fence violation. |
| C.2 boot evidence | ADOPT (validated) | Same checks; C2-boot.log 0 opencode; commit 7b0a102c3 @10:35:22 after boot 10:34:14–44. |
| C.3 move-only | ADOPT (validated) | All 21 moved paths blob-sha-identical C.2(source)→C.3(legacy/) (git rev-parse comparison, 21/21 OK). Diff shows pure-D for sources because Phase A already held identical legacy copies (overwriting with identical content = no A entry). |
| C.4 boot artifacts (worker#1, untrusted) | NOT adopted (worker#2 decision upheld) | Superseded by round-2 C.5 boot proof at final state. |

Note: worker#2's r1 doc slightly mis-stated the C.1 Makefile detail (thought only FILES
dropped in C.1); audit shows C.1 landed the FULL R5 edit (comment rephrase :171-174 +
FILES drop) — verified via `git show 2e3bf2b2a -- Makefile`. Gate `grep opencode Makefile`
= 0 at HEAD. Accepted commit-split deviation (plan put the comment rephrase in C.4;
landing it in C.1 is semantically complete and boot-safe).

## C.4 audit table (file → sites expected / verified / fixed)

| File (plan cite) | Expected sites | Verified | Fixed by auditor |
|---|---|---|---|
| tests/test_innate_skills_refactoring.py (P1 ×16) | 16 P1 cites (12 opencode mentions live at HEAD) | All 12 mentions cleaned; docstrings accurate; tuples already v2-shaped at HEAD (Phase B); grep-zero | 0 |
| tests/test_tool_filter.py (R1 ×3 tests + docstring) | :710-716, :750-764, :827-860 | 3 tests renamed+rephrased to "chart"; live map `INNATE_SKILL_TOOL_CATEGORIES["chart"]=["chart"]` (instance.py:159) makes the non-mock asserts semantically valid; mock-based e2e/deny tests self-contained; deny/no-op tests also rephrased (stricter than plan, harmless); grep-zero | 0 |
| tests/migration/test_jsonb_migration.py (R2 ×8 sites) | :68-69,:75,:106-107,:194-197,:223-226,:366-367,:497,:518 | module import dropped (collection fixed); 2 pair rows dropped; assert 17→15; both `__table__.create` fixture bodies dropped; _DO_BLOCK_SQL 2 rows dropped WITH correct trailing-comma fix on `('mcp_servers','config_schema')`; method import + table_to_class row dropped | **1**: worker#2's replacement header comment (:68-70) itself said "OpenCodeSessionRecord"/"opencode" ×3 → rephrased to token-free wording; grep-zero now holds |
| tests/unit/test_coder_agent.py (mirror :40-43) | drop external_opencode mirror | mirror entry dropped; contamination guards (:144,:168,:369+,:510,:253) UNTOUCHED (still assert absence — keep passing) | 0 |
| tests/unit/test_wanderer_agent.py (P2 mirror :41-44) | drop mirror | mirror dropped; :361-363,:410,:439 are class-(b) negative guards → left per plan | 0 |
| 12 class-(a) mock-strip files (21 patch sites) | wc_wake_off:293; council_tools:55; governor_integration:61+955; governor_recursion_acceptance_walk:336; spawn_team_members:58; spawn_instance_instructive_errors:26,42,50,362,368; auto_kill:992; send_message_context_param:106; send_message_load_skill:77; governor_recursion_guard:170; spawn_councilor:126; version_tag:432,1104,1292 | all 21 patch/mock lines dropped verbatim; instructive-errors rephrased "opencode"→"coder" (all @skip, self-contained mocked registry — behavior-neutral) | 0 |
| 6 cosmetic docstring sites | persistence_w1_markers:15; governor_recursion_acceptance_walk:35; spawn_team_members:42; send_message_context_param:84; send_message_load_skill:55; prompt_section_reference_integrity:815 | all 6 rephrased opencode-free (integrity fixture sample → "2-3 background jobs") | 0 |
| pyproject.toml (P3+R3) | :88 rephrase + testpaths | `testpaths = ["tests"]` ADDED; integration marker REPHRASED (row kept) | 0 |
| Makefile (R5) | :173-174 comment + :186 FILES | Already landed in C.1 (see R1 note); grep-zero at HEAD; no C.4 work remains | 0 |
| .env.example (:39) | drop "outgoing opencode prompts" | rephrased; env var stays (D5) | 0 |
| 9 R10 daemon comment/docstring files | config:168; keyword_extraction (doc l1+l9, logs :426,:430); context_injection:541+846; context_messages:35+39; critical_note_gate:8; waiting_children_watchdog:9+752; loader:226; task/repository:1777 | all rephrased opencode-free; code untouched (keyword_extraction module stays per D5); task/repository.py comment-only rephrase per R10 supersession | 0 |
| daemon/tools/instance.py (former-P7 ×3 + grant-map header) | :5823,:5860,:6246 + :155-156 | 3 trailing comments rephrased; grant-map header removed in C.1; grep-zero | 0 |
| 6 former-P7 stragglers | job_queue/conftest:65; critical_notes_phase2_filters:11; auth_boot_preflight:20; lcan mock_llm:141-142; regression_integration_test.sh:31,40; regression_chat_source:8 | all rephrased | **1**: regression_integration_test.sh replacement (:39-41) re-introduced "opencode" ×3 (named the retired pack + legacy/tests/opencode/ path) → rephrased token-free; Phase E bare-opencode gate safe |
| 4 cosmetic agent one-liners | explorer/soul; devops/soul; _prompt_system/knowledge:96,106; maintenancer 01-architecture-overview:6,16 | all rephrased; NONE of the five explicit base-pair dirs or [v2] dirs touched | 0 |

## Residual opencode map (active tree, post-audit)

- `tests/unit/test_devops_agent.py` (13 hits), `tests/unit/test_coder_agent.py` (9),
  `tests/unit/test_wanderer_agent.py` (5), `tests/unit/test_reviewer_v2_agent.py` (4):
  class-(b) NEGATIVE GUARDS — the opencode string IS the absence assertion; plan
  convergence addendum: "NOT yours; sibling allowlists". Left intact; guards keep passing.
- daemon/migrations/, data*/ DBs, legacy/, .agents historical, docs/mcp-kb-usage.md,
  plugins/opendesign vendored: per D5/D6/D7/Tier-5 exclusions — untouched.
- Everything else: grep-zero (R6 local pattern + bare token both verified).

## Semantic-deviation notes (accepted, non-blocking)

1. C.1 landed the R5 Makefile comment rephrase early (plan reserved it for C.4) —
   semantically complete, gate passes; noted in R1 section.
2. worker#2 removed the stale `assert "OpenCode-Skill" not in prompt` line in
   test_innate_skills_refactoring.py (giter branch). The remaining asserts
   (`"Coordination Skill" not in prompt`, `"# Skill" not in prompt`) are strictly
   stronger for "giter has no skills"; the removed assert referenced the retired
   token and had to go for grep-zero. Accepted.
3. worker#2 additionally rephrased the R1 "no-edit-needed" tests (deny + no-op tests in
   test_tool_filter.py) to chart-based fixtures — stricter than plan minimum, keeps the
   file grep-zero, semantics preserved. Accepted.
4. C.1 included a plan-external-but-necessary manager.py:6698 trailing-comma fixup for
   the JSONB IN-list SQL (documented in C.1 commit message; without it the DO block
   would not parse). Verified correct in C.1's committed diff.

## Pre-existing failure dispositions (re-verified pre-existence at this commit)

1. tests/test_innate_skills_refactoring.py:75 — leader fixture drift: fixture row
   `("leader", ["coordination","chart","todo"], ...)` (3 items) vs real
   `agents/leader/meta.json` `["coordination","chart","todo","question"]` (4 items).
   Fixture row byte-identical at base 885a19d4f → HEAD → worktree; leader/ meta.json
   unchanged across the whole mission (git diff empty). NOT C-caused; not fixed.
2. tests/test_agents_api.py:126,184 — mock drift (dispatcher: verified pre-existing by
   Phase B via git-stash round-trip; re-confirmed in round-2 gate run below).

## Guardrail pre-checks (auditor, before gate)

- `git status --porcelain -- 'agents/*[v2]*'` literal check: the mission's pathspec form
  matches 2 files via shell character-class quirk ('v' inside "devops"/"overview");
  literal `grep -F '[v2]'` over git status → ZERO. No writes under any `agents/*[v2]` dir.
- `git diff 885a19d4f -- daemon/migrations/` → 0 lines (append-only intact).
- tests/conftest.py: unchanged vs HEAD and vs base.
