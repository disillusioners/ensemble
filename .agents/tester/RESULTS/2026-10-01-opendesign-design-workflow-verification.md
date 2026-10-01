# opendesign design-workflow integration — verification report

**Date:** 2026-10-01 (runs 22:52–23:08Z)
**Commission:** Test the opendesign design-workflow integration
**Repo:** /home/nea/ensemble-src · **Branch:** `feature/opendesign-design-workflow` @ **`3b6ba8cd`** (base `b271e153`)
**Mode:** report-only (read-only + test execution; foreign dirty files untouched — all 5 workers verified dirty-set constant at 6 entries pre/post; no commits)
**Worker instances:** A `fe04e89e` (unit suite, test-pack-execution) · B `a040d682` (A/B regression, test-pack-execution) · C `d2e4843e` (static+schema) · D `39685f68` (prompt analysis) · E `d0ebbb55` (handoff dry-run)

## VERDICT: ✅ READY — 7/7 plan items PASS; 3 non-blocking minor findings (🟢); 0 regressions; 0 quick fixes needed

| # | Plan item | Verdict | Evidence (essentials) |
|---|---|---|---|
| 1 | Unit suite independent re-run | ✅ PASS | `timeout 300 .venv/bin/python -m pytest tests/unit/test_opendesign_builtin.py -v --tb=short -p no:cacheprovider` → **27/27 in 0.60s**, exit 0. HEAD verified `3b6ba8cd`; import trap clean (`daemon.__file__` → /home/nea/ensemble-src). |
| 2 | Prompt-registry A/B (base vs HEAD) | ✅ PASS | Same file at `b271e153` (detached worktree /tmp/odwf-base-wt, import resolved in-worktree) vs HEAD: **both legs `1 failed, 50 passed, 18 skipped`**, byte-identical failure set = exactly the known pre-existing `test_every_parent_agent_carries_scrutiny_guidance[designer]` (missing snippets `[REPORT SANITY:`, `interim, not completion`). No NEW failure. No dirty file under `agents/` (all 6 dirty entries under `frontend/` + untracked planning dir). Worktree removed; post-cleanup `git status --porcelain` byte-identical. |
| 3 | Byte-freeze on design-spec.md | ✅ PASS | `git diff b271e153 3b6ba8cd -- …/templates/design-spec.md` → **one hunk `@@ -92,6 +92,51 @@`, +45/−0**, adds `## Design artifacts` section only. `pinned_spec_sha` (front-matter line 16; occurrences at 5/16/32/158/164/165/181/187/191/215) — none inside the hunk window. |
| 4 | Schema functional (beyond unit pins) | ✅ PASS (a)–(d) | Real code exercised via /tmp script: **(a)** all 5 keys present (`byok_base_url, byok_api_key, byok_model, byok_provider, od_generate_timeout_ms`), all `required=False`, semantic required-set `[]`; **(b)** `byok_api_key` mirrors `od_api_token` byte-for-byte (opendesign.py:191-203 vs :218-230) incl. literal `__KMS_REF__<handle>__` + NEVER-plaintext, verbatim pass-through into `env.BYOK_API_KEY`; **(c)** all 5 env mappings correct; timeout string `"30000"` → `parse_config(...)['od_generate_timeout_ms'] == 30000` **int** (coercion via base.py:307-323 `_coerce_value`); **(d)** `build_config({})` → env exactly `{'OD_DAEMON_URL': 'http://127.0.0.1:7456'}` — zero new-key leakage (backward compat). |
| 5 | Degradation-path simulation (load-bearing) | ✅ PASS | Walk of designer Phase-4 with OD daemon DOWN + BYOK unprovisioned: every `od_*` call site covered by the defensive-dispatch wrap (`agents/designer/workflow.md:78` "every `od_*` call is wrapped so an exception or empty result triggers the text-lane fallback automatically"); text-lane fallback unconditional (`workflow.md:76`, mirrored design-strategy.md:64-78); write-through step is pure local I/O (no od dependency); no step requires an `od_url` to proceed. Degraded artifact row fully specified: `kind: text-mockup` (template design-spec.md:128; architecture-recommendation.md:198), `mockup_lane: text` (workflow.md:76), `od_url` absent/em-dash (arch :200; dev[v2]:141), `lint: n/a` (workflow.md:76), path under canonical `mockups/`. **No dead-end.** |
| 6 | Handoff dry-run (mock, /tmp) | ✅ PASS (a)(b)(c) | Real 168-line HTML mockup + implement-brief fragment at /tmp (repo untouched). **(a)** canonical convention `.agents/shared/planning/{feature}/design/mockups/<page>.html` documented unambiguously — 8 citations across 4 files (workflow.md:66,73,76; design-spec.md:101,127; developer/workflow.md:118,124; developer[v2]:140-141). **(b)** developer base Step 4b (`agents/developer/workflow.md:122-138`) fully actionable: 6/6 steps walk against the real file; AC greps executable (`class="card"`, `class="btn"` all hit); escalation rule :138 not triggered. **(c)** v2 relay (`developer[v2]/workflow.md:141`) carries `{path, kind, ac_refs, od_url, lint}` + `mockup_lane` verbatim — no field missing/renamed/altered. |
| 7 | md-lint / structural closure | ✅ PASS | No markdown lint tool exists in repo (scripts/, Makefile, package.json, pyproject — none). Closure grep per `docs/agent-prompt-writing-guide.md:108` on the 5 changed prompt files: **14 hits, all justified operational filesystem paths** (artifact write/read targets, template opens, tool-parameter values); zero forbidden `workflow.md`/`rule.md`/`soul.md`/`tools_note.md`/`memory.md` prompt-section tokens. `See <Section>` cross-refs: **13/14 RESOLVED, 1 PARTIAL** (see findings). |

## ensure.md status (scoped by blast radius)

- **Core #1** (no regressions in changed packs): ✅ PASS — item 1 pack 27/27; item 2 A/B base-identical (single pre-existing RED, not commission-caused, not grown).
- **Core #2/#3** (concurrency_atomic_unit_test): **N/A — scoped out.** Change set = MCP builtin config schema + prompt files + tests; no concurrency/execution-lane code touched.
- **Core #4** (dev.sh `--timeout-graceful-shutdown 10`): ✅ PASS — present at dev.sh:102 (grep evidence).
- **Release Gate**: not run — not warranted (feature addition; not cross-module architecture refactor or release).
- No contradictions between ensure.md methods and pack rules in this run.

## Minor findings (🟢 non-blocking — invite, don't demand)

1. **v2-only `render` kind enum value.** `agents/developer[v2]/workflow.md:141` enumerates `kind: html-mockup | text-mockup | render`, while `design-spec.md:127-128` and base developer workflow enumerate only `html-mockup`/`text-mockup`. Additive divergence, not a contract break (no rename/loss); a follow-up one-line sync in design-spec.md would close it.
2. **`text-mockup` literal not inline in designer prose.** The literal kind token appears only in the design-spec template (which the designer opens from, workflow.md:58) and architecture §4.5 (cross-referenced at workflow.md:80 / design-strategy.md:80) — not spelled out in workflow.md/design-strategy.md body text. An agent skipping the §4.5 cross-ref would still see it in the template it opens; practical exposure low.
3. **`My Rules` informal alias.** `agents/designer/workflow.md:40` references "see `My Rules` — Brief Validation": `Brief Validation` resolves (rule.md:20), but `My Rules` is not a literal heading anywhere (target is rule.md's Cardinal/Guideline sections). Lenient reading obvious; strict heading-resolution reading partial.

## Pre-existing RED (unchanged, out of scope)

`tests/unit/test_report_integrity_prompts.py::test_every_parent_agent_carries_scrutiny_guidance[designer]` — designer agent missing `[REPORT SANITY:` / `interim, not completion` scrutiny snippets. **Base-identical at b271e153 (1F/50P/18S both legs)** — pre-existing prompt-content gap for the designer-agent owner, NOT caused or grown by 3b6ba8cd. Deterministic (not flaky) → does not qualify for QUARANTINE.md; ticket to designer-agent owner.

## Runtime & hygiene

- Total wall-clock ≈ 20 min (5 parallel workers, longest leg ~2.5 min). All pytest legs ≤ 0.74s under `timeout 300` wrappers; no TIMEOUTs anywhere.
- All /tmp scratch removed and verified (`/tmp/odwf-base-wt`, `/tmp/odwf-handoff/`, `/tmp/odwf-schema-check.py`); worktree pruned; every worker confirmed repo dirty-set byte-identical pre/post.
- No code changes, no commits (report-only commission; leader owns git).

**Overall: READY — commit 3b6ba8cd validated for merge consideration on this evidence.**
