# tests/unit bucket B3 — pack run (5 of 5 buckets)

**Date:** 2026-10-22 (run 2026-10-23 02:19 / 02:24 +07)
**Branch:** `feature/mcps-gateway-interop` @ `0b26f515d`
**Scope:** `tests/unit` bucket B3 of 5 — 158 paths, census-verified 2,841 nodes
**Verdict:** **PASS** (post-fix: 11 failed / 2801 passed / 29 skipped; all 11 ledgered, zero unmatched regression candidates)

---

## 1. HEAD / ancestry disclosure

| Fact | Value |
|---|---|
| HEAD at run | `0b26f515d` — *test(job-queue): make stale-task wedge hook test loop-independent* |
| Ancestry | `git merge-base --is-ancestor 96bd8f695 0b26f515d` → **YES** (96bd8f695 is an ancestor of HEAD) |
| Diff scope `96bd8f695..0b26f515d` | **1 file, test-only**: `tests/job_queue/test_round2_council_fixes.py` (+1/−1, `asyncio.get_event_loop()` → `asyncio.run()`) |
| B3 impact of that commit | **ZERO** — `tests/job_queue/` is outside bucket B3's 158 paths |
| Action | Proceeded (authorized test-only commit, ancestry confirmed, no B3 overlap) |

## 2. Environment

| Item | Value |
|---|---|
| `timeout` | GNU coreutils 9.10 (`/opt/homebrew/bin/timeout`) — present, no BSD-portability gap |
| python | 3.13.3 (`.venv`), pytest 9.0.2, pytest-xdist 3.8.0 |
| PG | `127.0.0.1:5432` OPEN; db `ensemble_dev_test_only` reachable as user `ensemble` |
| `uv run` | resolves offline in 0.1 s (no re-sync) |

## 3. Invocation (single pack, dual-layer timeout)

```
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble && \
FILES="$(cat /tmp/b3_files.txt)" && \
timeout 300 env -u DATABASE_URL POSTGRES_HOST=127.0.0.1 \
  POSTGRES_DB=ensemble_dev_test_only ENSEMBLE_SELF_ENV=dev DAEMON_PORT=8079 \
  uv run python -m pytest $FILES -n 5 -q --tb=short
```

- Outer guard: GNU `timeout 300` (command level). Inner guard: pytest's own per-test timeout
  (pyproject `timeout` plugin) + xdist worker isolation.
- No `-x` — all failures reported. No other pack or test executed.
- Ports 8088 / 9797 untouched. `/Users/nguyenminhkha/All/Code/agents-ensemble` untouched.
  Nothing killed. No `git add -A`.

## 4. Collected-vs-census reconciliation

| Run | Collected | Failed | Passed | Skipped | Arithmetic |
|---|---|---|---|---|---|
| Pre-fix | **2841** | 16 | 2796 | 29 | 16 + 2796 + 29 = **2841** ✓ |
| Post-fix | **2841** | 11 | 2801 | 29 | 11 + 2801 + 29 = **2841** ✓ |

Census 2,841 nodes **exactly matched** in both legs. The rename in the post-fix leg
(`test_skill_injection_enabled` → `test_skill_injection_disabled`) is count-neutral.

## 5. Runtime

| Leg | Wall | pytest-reported | Exit |
|---|---|---|---|
| Pre-fix | 44 s | 41.68 s | 1 |
| Post-fix | ~41 s | 39.46 s | 1 |

Both far inside the 300 s cap (≈15% of budget). 158 paths / 2841 nodes in ~42 s with `-n 5`.

## 6. Failure classification — all 16 pre-fix reds mapped to QUARANTINE.md Active rows

Read first: `.agents/tester/QUARANTINE.md` (158 lines; Active table = lines 6–144, Resolved = 145+).

### LEDGERED — test-side, stale expectation vs a deliberate production commit → FIXED (5 nodes)

| # | Node | Family (ledger row) | Production commit | Fix |
|---|---|---|---|---|
| 1 | `tests/unit/test_gaia_agent.py::TestGaiaRegistryDiscovery::test_gaia_tools_config_in_registry` | gaia tools.allow extra-`service` ×3 (row 2026-10-07) | `3e46576dc` *feat(service-tool): per-agent meta-grants — append 'service' to bash/proc-capable agents* | expected list += `"service"` |
| 2 | `tests/unit/test_gaia_agent.py::TestGaiaToolFiltering::test_gaia_tool_filter_from_registry` | ↑ same | ↑ same | ↑ same |
| 3 | `tests/unit/test_gaia_agent.py::TestGaiaToolFiltering::test_gaia_tool_filter_config_parsed_correctly` | ↑ same | ↑ same | ↑ same |
| 4 | `tests/unit/test_devops_agent.py::TestDevopsMetaJsonValidation::test_skill_injection_enabled` → **renamed** `test_skill_injection_disabled` | devops meta drift ×3 (row 2026-09-09 / 2026-09-14) | `e56070de6` *disable skill injection for devops* (2026-08-24) | assert `is False`; docstring states the opt-out |
| 5 | `tests/unit/test_devops_agent.py::TestDevopsPromptComposition::test_no_opencode_skill_content_in_system_prompt` | ↑ same (chart innate member) | `f4d3be5db` *feat: add ask_questions and generate_chart tools to devops agent* | forbidden set += `"chart"` |

Intra-file proof for #5: the sibling node `test_innate_skills_is_dynamic_skill_and_todo` already
asserts `innate_skills == ["dynamic-skill", "chart", "todo"]` and **passes** — the forbidden-set
literal was the stale one.

### LEDGERED — production-side → REPORT ONLY (11 nodes, unchanged by this run)

| # | Node(s) | Family (ledger row) | Production surface |
|---|---|---|---|
| 6–7 | `tests/unit/tools/test_instance_tools.py::TestRegistration::test_planner_and_tester_resolve_subtree_messages_via_registry` (`:4423`)<br>`::TestSubtreeStatusRegistration::test_leader_planner_tester_opt_in_via_production_seam` (`:5309`) | planner subtree allow ×2 (row 2026-10-17) | `agents/planner/meta.json` `tools.allow` lacks `subtree_messages` + `subtree_status`. Ledger: "production config, out of test-lane scope" |
| 8–9 | `tests/unit/tools/test_knowledge_tools.py::TestExploreCallerModelOverrides::test_coder_with_null_override_forwards_system_default_model` (`:3021`)<br>`::test_coder_with_string_override_forwards_override_model` (`:3060`) | knowledge caller-model ×2 (row 10) | `daemon/tools/knowledge_tools.py` does not forward `model=` into `invoke_agent_and_wait` — assertion `None == 'gpt-4o'` |
| 10 | `tests/unit/tools/test_snapshot_tools.py::TestRegistrationChain::test_gate_flag_enabled_on_targets` (`:1835`) | snapshot gate drift ×1 (row 10) | `snapshot_enabled: true` on **both** `agents/developer/meta.json` and `agents/developer[v2]/meta.json` → `['developer','developer','tester']`. The node's own docstring says the flag "must live on the versioned variant" — the unversioned duplicate is the production defect |
| 11–13 | `tests/unit/test_maintenancer_agent.py::TestConventionCompliance::test_all_prompt_files_exist` (`:411`)<br>`::test_no_forbidden_system_tokens[memory.md]` (`:441`)<br>`::test_prompt_file_non_empty[memory.md]` (`:415`) | memory.md ×3 (row 10) | `agents/maintenancer/memory.md` archived (never deleted) by sweep `3c307e8d4` (user directive 2026-10-14, ruling R2). Zero of 11 agents still carry `memory.md`; prompt-writing guide lists it as **Optional** |
| 14–15 | `tests/unit/test_project_manager_agent.py::TestPromptComposition::test_soul_cites_rule_for_severity_framing` (`:1004`)<br>`::test_workflow_cites_soul_and_rule` | project_manager prompt cross-refs ×2 (row 2026-10-07) | `agents/project-manager/soul.md` does not cite `rule.md`; `workflow.md` cites neither `soul.md` nor `rule.md` |
| 16 | `tests/unit/test_maintenancer_kb_coverage.py::test_release_tag_pin_matches_pyproject` (`:259`) | release-tag pin (rows 2026-09-14 / 2026-10-12 / 2026-09-28) | `EXPECTED_RELEASE_TAG="v0.12.4"` vs `pyproject.toml` `0.27.1`. **Not a 1-line test fix**: the 6 KB docs (`agents/maintenancer/knowledge/0*.md`) all pin `last-verified-against: v0.12.4` — bumping the constant alone would move the failure to the doc-pin loop. Correct fix = re-verify 6 production KB docs against v0.27.1 (root cause per ledger: a release bump forgot the pin) |

### UNMATCHED REGRESSION CANDIDATES

**None.** Zero reds failed to map to a ledger row. No branch-introduced failure was found in B3.

## 7. Quick fix — commit `6e2f0d5c8`

`test: align gaia/devops meta expectations with shipped agent config`

```
 tests/unit/test_devops_agent.py | 30 +++++++++++++++++-------------
 tests/unit/test_gaia_agent.py   |  6 +++---
 2 files changed, 20 insertions(+), 16 deletions(-)
```

Test code only — **no production file touched**. Each individual fix is < 20 lines.
Node-count neutral. Per-fix verification: `tests/unit/test_gaia_agent.py tests/unit/test_devops_agent.py`
→ **98 passed in 0.92 s**.

Staging discipline: the working tree carried **concurrent, non-mine** modifications from other
sessions (`.agents/approver/active.md`, `.agents/approver/mcps-gateway-interop-tracking.md`,
`.agents/charter/`, `.agents/shared/planning/mcps-gateway-interop/*`,
`.agents/tidier/notes.md`, two `.agents/tester/RESULTS/*`). Only the 2 edited test files were
staged (`git add <2 explicit paths>`); **no `git add -A`**.

Commit identity: repo-local `.git/config` still overrides to `Councilor C2 <counselor@c2.local>`
(known open project issue — a concurrent session is actively committing under it, left untouched).
This commit was stamped with the global identity via `git -c user.name=Worker -c user.email=worker@ensemble.local`
— per-commit override only, shared config not mutated.

## 8. Hygiene

- No daemon stopped, no process killed, no port 8088/9797 touched.
- No second pack, no second bucket, no bare `pytest tests/`.
- No `git add -A`; no production file edited.
- `QUARANTINE.md` **not modified** — the ledger is shared and other buckets ran concurrently;
  the 5 newly-green nodes are reported here for the dispatcher to fold in.
