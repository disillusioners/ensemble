# Verification Surface — designer-critic-orchestration (tester run guide)

**Pipeline under verification:** `designer → sketcher → critic → designer accept/save`. All commands run from the worktree `/home/nea/ensemble-src-wt-designer-critic-orchestration` with the venv interpreter (`/home/nea/ensemble-src/.venv/bin/python3 -m pytest` — system python3 lacks pydantic; the worktree has no .venv).

## 1. Nit-2 pytest invocations (architecture-recommendation.md §3 Nit 2)

Run individually (`-k` patterns) or as the canonical batch at the end of this section. Each names its objective + PASS criterion.

- `pytest -k "critic_meta"` — critic meta.json shape per canonical D6 (bare allow, image_save/mcp/hard refusals denied, view-views absent, leaf team, vision lane, no default_queue/watchover). PASS = 1 passed.
- `pytest -k "critic_tools_resolve"` — THE WRITE-LEAK GATE: resolves critic's filter through the real daemon seam (factory-created image/design tools → `scan_tools_for_full_docs` → `resolve_tool_filter`) and asserts the resolved set EQUALS the EXACT 5-tool enumeration `{read_file, image_get, image_list, explain_image, compare_images}`. An AC pinned at "4+1" can never pass because `image` resolves to four tools incl. `image_save` — the explicit deny strips it. PASS = 1 passed.
- `pytest -k "critic_deny_wins"` — no write-capable tool (`write_file`/`edit_file`/`image_save`) survives the filter. PASS = 1 passed.
- `pytest -k "critic_team_implied"` — DECLARED `team_members: []` + EFFECTIVE auto-extension of `image-comparator` per the `design` allow entry (`TOOL_REQUIRED_AGENTS["design"] == ["image-comparator"]`). PASS = 1 passed.
- `pytest -k "designer_od_generate_removed"` — the inverse rewire pin: designer holds zero `od.*` tokens across meta/soul/rule/tools_note/skill; the four ports pinned on sketcher (meta grant + prose). PASS = 1 passed.
- `pytest -k "parity_runs_v2_schema"` — lane enum `{"sketcher","critic"}` (drop `direct`), HTML-comment header line skipped, `critic_verdict` field rejected. PASS = 1 passed.
- `pytest -k "agent_registry_scan"` — new-agent-dir discoverability: real `get_registry().exists('critic')` probe in a subprocess pinned to the repo root. PASS = 1 passed (subprocess returncode 0).
- Canonical batch: `pytest -k "critic_meta or critic_tools_resolve or critic_deny_wins or critic_team_implied or designer_od_generate_removed or parity_runs_v2_schema or agent_registry_scan" tests/unit/plugin_subsystem/test_designer_rewire.py tests/unit/agents/test_sketcher_agent.py -q` — PASS = exactly 7 passed.

Supplemental report-integrity gates:

- `pytest -k critic tests/unit/test_report_integrity_prompts.py` — critic is a leaf (`team_members: []`), so the registry-completeness parent-scrutiny check SKIPS it by design. PASS = exit 0 (1 skipped is the expected green).
- `pytest -k designer tests/unit/test_report_integrity_prompts.py` — designer keeps the (d) report-scrutiny guidance on its non-empty team. PASS = 1 passed.

First-run source anchors (approver iteration-002 note — verify these cites on first run; the plan did not pre-verify): `compare_tools.py:1265-1269` (pinned_spec_sha binding), `registry.py:1191/:1207` (`exists()`/`get_registry()`), `_auth.py:155-161` (design→image-comparator auto-extension; the mapping table sits at `:38-50`).

## 2. Expected stderr shapes (do NOT false-flag)

- The pre-phase-2 `'critic'` registry positive control fails with an **`AssertionError` traceback** — by-design failure proving the gate is real; a tester grepping `traceback` must not flag it as unexpected.
- `Agent 'maintenancer': deny entry 'git_commit'…` — pre-existing daemon config-scan warning, expected noise on every registry probe (plan-overview SC-9 / R23).

## 3. Grep gates (the 21 SCs — plan-overview.md §7)

- SC-1 `grep -cE '"od\.[a-z_]+"' agents/designer/meta.json` → 0
- SC-2 `jq '.team_members | index("critic")' agents/designer/meta.json` → non-null
- SC-3 `grep -c "single-page one-offs" agents/designer/workflow.md` → 0; `grep -cE "stay on my own direct|run it on my own direct" agents/designer/workflow.md` → 0
- SC-4 `grep -cE "^## Dual-Run Pilot" agents/designer/workflow.md` → 0
- SC-5 `grep -cE "tool-not-bound\|call-error\|timeout\|daemon-unavailable\|other:" agents/designer/rule.md` → 0 (false-green literal-pipe form) AND `grep -cE "tool-not-bound|call-error|timeout|daemon-unavailable|other:" agents/designer/rule.md` → ≥2
- SC-6 mechanical: SC-2's python assert form (phase2 AC2 command)
- SC-7 `grep -cE 'meta\.json|tools\.allow|daemon/|skill-set\.yaml|seeder|version registry|test paths' agents/critic/soul.md` → 0
- SC-8 `grep -cE '^[0-9]+\.' agents/critic/rule.md` → ≤7 (expect 7); four-anchor grep ≥4
- SC-9 probe: `/home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"` → 0
- SC-10 = supplemental report-integrity `-k critic` (section 1)
- SC-11 `grep -cE "sketcher.*critic|critic.*sketcher" agents/designer/workflow.md` → ≥1
- SC-12 `pytest tests/unit/plugin_subsystem/test_designer_rewire.py` → PASS (24 passed)
- SC-13 `git log feature/designer-critic-orchestration --oneline -n 10` → four phase commits
- SC-14 `sha1sum /home/nea/ensemble-src/agents/designer/meta.json` matches pre-commission hash `06b8bf641911ebd1abb5f734b5ea872e9617eb2b`; `git diff feature/designer-critic-orchestration -- agents/designer/meta.json` shows the diff lives in the worktree branch only
- SC-15 `jq '.tools.allow | index("design")' agents/critic/meta.json` → non-null; `jq '.tools.allow | index("view-views")' agents/critic/meta.json` → null
- SC-16 `git diff feature/designer-critic-orchestration -- daemon/tools/_tool_registry.py` → empty
- SC-17 `head -1 .agents/shared/planning/od-generate-agent-lane/stage2-addendum.md` → the SUPERSEDED marker; line count = 112 (original 111 + 1)
- SC-18 `grep -c "single-page one-offs" agents/designer/soul.md` → 0; `grep -cE "stay on my own direct|run it on my own direct" agents/designer/soul.md` → 0
- SC-19 `grep -cE "^## Review|^## Verdict|verdict:\s*(pass|needs-revision)" agents/critic/workflow.md` → ≥3; `wc -l agents/critic/workflow.md` → ≤60
- SC-20 `grep -cE '^[0-9]+\.' agents/designer/rule.md` → 7 (≤7); `grep -cE "shared_meta_kv|round_count|verbatim|VISUAL-QA-DEFERRED" agents/designer/rule.md` → ≥3
- SC-21 `grep -cE "\[BRIEF-LEVEL\]|prev_attempt_unparseable|verdict:\s*\((pass|needs-revision)\)|pinned_spec_sha" .agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md` → ≥4 (expect 10)

## 4. Manual review checklist

- File existence: `agents/critic/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}` present; no `skill-set.yaml` (decision: NO).
- Byte-count plausibility: designer soul 86→86 lines; tools_note 172→79; sketcher tools_note 35→124 (capture-recipe migration −93/+89 accounted).
- Cross-file lockstep: workflow Phase 4 + Mockup Lane + tools_note OD section render the same orchestrator framing (zero `od.*` in all three).
- Criterion cardinality: critic rule.md ≤7 numbered Cardinals (7).

## 5. Optional smoke (NOT required)

A live designer dispatch on a trivial brief is optional and only if a sandbox instance is available — zero live OD/LLM calls are required by this verification surface; every gate above is unit-level and minutes-bounded.
