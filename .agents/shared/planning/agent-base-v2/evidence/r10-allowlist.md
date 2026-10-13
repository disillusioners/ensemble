# R10 Allowlist — agent-base-v2 mission (docs surface)

**Date:** 2026-10-13
**Authority:** architecture-recommendation.md §11 (binding user directive, 2026-10-13)
**Phase D context:** every active-tree doc row that describes our service is a MANDATORY FIX unless it falls into the §11.3 exclusion set below.
**Gate reviewer (Phase E FINAL TESTER GATE):** rows without justification FAIL gate review.

## Exclusion classes (per R10 §11.3 / §11.4)

| Class ID | Description |
|---|---|
| D6 | Strictly-upstream false positive (documented upstream product, NOT our service) |
| R10.4#2 | Vendored third-party (lives under `plugins/`, shipped in release payloads; cannot move to `legacy/`) |
| R10.4#3 | Intentional-keep (functional configuration that must remain) |
| R10.4#4 | `.kilo/` (executor-classified at gate time; gate-excluded via `':(exclude).kilo'`) |
| HIST | Historical / audit material (out of mission scope) |
| P4 | Stale-HISTORY (per architecture-recommendation §6 P4) |

## Rows (one per excluded path:line — D6 false positive, R10 §11.4 #2-4, historical, P4)

| Path | Class | Justification (required by R10) |
|---|---|---|
| `docs/mcp-kb-usage.md:12` | D6 | Documents the upstream `sst/opencode` user's MCP config file path (`~/.config/opencode/opencode.json`) — not our service. Line-by-line strictly-upstream verification at edit time; no our-stack reference. |
| `docs/mcp-kb-usage.md:13` | D6 | Same — upstream `sst/opencode` project-level config (`.opencode.json`). Not our service. |
| `docs/mcp-kb-usage.md:34` | D6 | Upstream `sst/opencode` JSON-schema URL (`https://opencode.ai/config.json`). Not our service. |
| `docs/mcp-kb-usage.md:46` | D6 | Upstream `sst/opencode` `oh-my-opencode-slim` plugin name. Not our service. |
| `docs/mcp-kb-usage.md:48` | D6 | Upstream `sst/opencode` MCP config interaction note. Not our service. |
| `docs/mcp-kb-usage.md:50` | D6 | Upstream `sst/opencode` config file path. Not our service. |
| `docs/mcp-kb-usage.md:73` | D6 | Upstream `sst/opencode` config naming convention. Not our service. |
| `docs/test-suite-known-issues.md:43` | HIST | Historical pointer to retired test path `tests/opencode/test_integration.py`; the test tree was archived to `legacy/tests/opencode/` in Phase A. The line is a historical record; out of Phase D scope (Phase C follow-up may rewrite). |
| `docs/plans/shared-context-metadata-message-injection.md:107` | HIST | Past planning record referencing `external_opencode.py`; the file no longer exists post-Phase-C. The plan doc is a historical artifact. |
| `docs/plans/decouple-execution-plan.md:107` | HIST | Past planning record listing `opencode/state.py` in a 19-file audit list; the file was removed in Phase C/2. Historical. |
| `docs/plans/decouple-execution-plan.md:512` | HIST | Same plan doc, same historical 19-file audit list. Historical. |
| `docs/plans/decouple-execution-plan.md:574` | HIST | Past planning record with a `plan-track opencode session` mention; the entire opencode integration is retired. Historical. |
| `docs/plans/cleanup-old-architecture.md:250` | HIST | Past planning record enumerating a 19-file sweep including `opencode/state.py`; the file is now archived. Historical. |
| `docs/plans/decouple-review.md:59` | HIST | Past planning record with an 18-file audit list including `opencode/state.py`. Historical. |
| `docs/plans/skill-metrics-improvement.md:442` | HIST | Past planning record describing a proposed `opencode` innate skill for delegated code execution; the skill is retired, the feature is not built. Historical. |
| `docs/bugs/agent-call-wrong-tool-spawn-session.md:4` | HIST | Historical bug record — an LLM called an agent with `agent_id=opencode` (a skill, not an agent). The bug is fixed. Historical. |
| `docs/bugs/agent-call-wrong-tool-spawn-session.md:8` | HIST | Same historical bug record; the post-fix explanation. Historical. |
| `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.review.md:367` | HIST | Historical review of a concurrent-task bug; the `opencode_skill` mention is incidental. Historical. |
| `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md:107` | HIST | Historical bug record containing a quoted LLM message that mentions "the current opencode skill uses…". Historical. |
| `docs/bugs/job-feedback-observer-cross-instance-handoff.md:21` | HIST | Historical incident log; an `external_opencode` session timestamped event. Historical. |
| `docs/bugs/job-feedback-observer-cross-instance-handoff.md:22` | HIST | Same historical incident log; the `external_opencode_wait_for_result` tool call. Historical. |
| `docs/bugs/skill-feedback-no-usage-record-in-production.md:66` | HIST | Historical bug analysis identifying `self._opencode_engine` as the only side-channel engine; the engine was removed in Phase C. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:9` | HIST | Past optimization proposal; references `opencode preload` as a comparison point for the proposed fix. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:42` | HIST | Same proposal; row 3 of a comparison table. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:128` | HIST | Same proposal; describes the `opencode dispatch path` and the `_preload_shared_context()` mechanism. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:138` | HIST | Same proposal; the `related_context_keywords` parameter design. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:196` | HIST | Same proposal; a work-item table row. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:206` | HIST | Same proposal; phase-2 sprint plan. Historical. |
| `docs/optimization/workflow-context-load-optimization.md:226` | HIST | Same proposal; pointer to `daemon/tools/external_opencode.py:419-539` (file removed in Phase C). Historical. |
| `docs/optimization/workflow-context-load-optimization.md:227` | HIST | Same proposal; pointer to `daemon/tools/external_opencode.py:622-633` (file removed in Phase C). Historical. |
| `docs/optimization/workflow-context-load-optimization.md:314` | HIST | Same proposal; the top-priority work item. Historical. |
| `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` | R10.4#2 | Vendored third-party design-system resource; provenance class `copy_freely` per `plugins/opendesign/`. The `plugins/` tree is shipped in release payloads (`stage.sh:407`) and cannot move to `legacy/` without breaking the OD plugin snapshot. NOT a user-decision item. |
| `.gitignore` opencode lines (e.g. `opencode_sessions.db` ignore) | R10.4#3 | INTENTIONAL-KEEP for D7 orphan-DB safety: any leftover `opencode_sessions.db` file from a partial uninstall must remain gitignored. |
| `.kilo/` (66 lines across tracked files, per W4 dry-run) | R10.4#4 | Executor-classified at gate time per architecture-recommendation §11.4 #4 — historical tool state → allowlist; active config → fix. Gate-excluded via `':(exclude).kilo'` in the R10 FINAL TESTER GATE (per W4 review consistency check). |
| `docs/incidents/**` | HIST | Historical incident records; out of mission scope. |
| `docs/bugs/opencode-session-manager-memory-leak.md` (moved to `legacy/docs/bugs/` per addendum MOVE list) | HIST | Two opencode bug docs were moved to `legacy/docs/bugs/`; pre-archive they described retired stack behavior. The originals are no longer in the active tree. |
| `docs/bugs/external-opencode-wait-latency.md` (moved to `legacy/docs/bugs/` per addendum MOVE list) | HIST | Same as above — moved to legacy archive in addendum MOVE list. |
| `.agents/tester/PACKS.md:922,:957,:1272,:2157` | P4 | Stale-HISTORY references to retired pack basenames (per architecture-recommendation §6 P4); no runner enforces presence. **BOTH gate-excluded** via `':(exclude).agents/tester/PACKS.md'` **AND allowlisted** — no contradiction (per W4 review). |
| `test/packs/regression_integration_test.sh:40` | P4 | Same as above — stale-HISTORY pack reference. |

## Census (R10 §11.8)

Total rows: **37** (one per path:line or per directory glob).

Class distribution:
- D6 (strictly-upstream false positive): 7 rows — `docs/mcp-kb-usage.md`
- R10.4#2 (vendored third-party): 1 row glob — `plugins/opendesign/copy_freely/design-systems/opencode-ai/**`
- R10.4#3 (intentional-keep): 1 row — `.gitignore` opencode lines
- R10.4#4 (executor-classified at gate time): 1 row — `.kilo/`
- HIST (historical/audit material): 25 rows — `docs/test-suite-known-issues.md:43`, `docs/plans/*`, `docs/bugs/*`, `docs/optimization/*`, `docs/incidents/**`
- P4 (stale-HISTORY): 2 row globs — `.agents/tester/PACKS.md`, `test/packs/regression_integration_test.sh`

No orphan class remains (per R10 §11.8 census requirement). Every allowlist row is one of (a) D6 false positive, (b) vendored third-party, (c) intentional-keep for D7, (d) historical/audit material, (e) P4 stale-HISTORY with justification.

## Verification

- The union of stream(i) + stream(ii) + stream(iii) residual (31 + 3 + 0 = 34 hits) is fully enumerated in the rows above.
- No allowlist row describes CURRENT active-stack behavior of OUR service (R10 §11.3 hard rule: any such row would be FIXED, not excluded).
- D6 rows were line-by-line strictly-upstream verified at edit time: no line in `docs/mcp-kb-usage.md` references our service.
- R10.4#2 (vendored) lives under `plugins/` (per `stage.sh:407`); the vendored resource is the upstream `sst/opencode-ai` design-system content, not our wiring.
- HIST rows either name retired files (e.g. `daemon/opencode/state.py`, `daemon/tools/external_opencode.py`) or describe past bugs/incidents/proposals; none refer to current active-stack behavior.
