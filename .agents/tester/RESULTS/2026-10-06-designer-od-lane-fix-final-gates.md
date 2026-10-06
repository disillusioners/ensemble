# designer-od-lane-fix — FINAL GATES (pre version-staging + merge)

**Date:** 2026-10-06
**Commission:** final code-side barrier before v0.17.x staging + merge; both review streams APPROVED
**Worktree:** `/home/nea/ensemble-src-wt-odlane-fix` · branch `feature/designer-od-lane-fix` · base `latest@c55f2b0a`
**Stack under test:** `666c6483` → `037adb83` → `e5af15ca` → `b1e32252` (+ tester-added test-only seam commit `0859ee53`, see G2b)
**Workers:** odlane-g1-ab (d335d555, 6 dispatches), odlane-g4-static (6ddca63e), odlane-g3-live (07f36fd8, 3 dispatches), odlane-g5-fences (d9f2de1f)
**Defect gated:** spawn-lane MCP-preload asymmetry — agent-tool `spawn_instance` lane + 5 sibling bare sites skipped MCP preload → designer children bound ZERO MCP tools on live (RCA 2026-10-05, 4/4 dispatches since Oct 1). Fix = at-lane preload (pre-gen UUID → `await ensure_mcp_preloaded` → `instance_id` into sync spawn) + `is_preloaded()` + warn-once miss guard.

---

## Verdict summary

| Gate | Verdict | One-line evidence |
|---|---|---|
| G1 discrimination A/B | ✅ PASS | base: 4 mechanism-anchored FAILs (`is_preloaded` never written for child UUID; warn-once 0-fires; fix-only symbol ImportError) + probe T1 FAIL citing the 6 bare sites; HEAD: 10/10 + probe PASS; restoration byte-clean; FakeManager FAITHFUL |
| G2a previously-red-12 | ✅ PASS | 47/47 green across test_council_tools.py (28P) + test_version_tag_tool_resolution.py (19P) @ 0859ee53 |
| G2b wider-surface delta | ✅ PASS (post-remediation) | 142-file/3,958-test symmetric A/B, 11 chunks ×2 states: net-new at HEAD = ∅ after test-only seam commit `0859ee53`; residual delta = exactly 4 by-design base-only seam pins (stack-edited test files = G1-class discrimination); collection-error delta ∅; pre-existing families identical (spawn_limit 9=9, spawn_team_members 18=18, load_skill 7=7) |
| G2c related modules | ✅ PASS | 187P/1S/0F across test_mcp_service(73), test_mcp_lazy_init(22), test_mcp_tool_filter(25), test_mcp_cold_load_race(6), test_job_processor(33), test_sources_mapper(29) |
| G3 live F1 binding | ✅ PASS | real lane end-to-end: preload-before-spawn proven (L1128 < L1133 < L1135), 12/12 dev-lane MCP tools bound, real od_list_projects 315ms healthy (Step-0-first observed), warn-once 5/5, teardown clean |
| G4 prompt/spec gates | ✅ PASS 6/6 | enum verbatim ×4 sites; template columns REQUIRED/n-a; Step-0 probe workflow.md:72 + template:118; zero text-first residue; skill 1.3.0 lockstep; tools_note 10/10 od_* + anti-pattern-only npm/npx; BONUS Step-0-fires-first confirmed live |
| G5 scope fences | ✅ PASS | daemon/ = exactly the 4-file allowed set; 8 test files; 6 designer prompt files; zero frontend/meta.json/other-agents; 5-commit stack shape; dev.sh `--timeout-graceful-shutdown 10` ✓ |
| **OVERALL** | 🟢 **SHIP** | 7/7 gates green at `0859ee53` (stack `666c6483→037adb83→e5af15ca→b1e32252` + tester seam commit `0859ee53`); proceed to version staging + merge |

**Scope note:** commission-mandated gate set — every gate was in scope by instruction; no scope reduction applied. ensure.md Core scoped to the change set: changed-packs regression (G1/G2 evidence) + `dev.sh` static check (G5 rider) — both green.

---

## G1 — Discrimination re-verification: PASS

**Method:** checkout-path + patch-file A/B (⛔ stash banned, honored — zero stash ops all run). daemon/ flipped to `666c6483` with tests held at HEAD; restoration to `b1e32252` proven byte-clean (`git status --porcelain daemon/` empty + HEAD unchanged + diff-empty, re-verified after HEAD runs). Venv import resolved inside the worktree (editable-pth trap checked every phase).

**Base-state (daemon/@666c6483):** `timeout 300 .venv/bin/python -m pytest tests/unit/test_spawn_lane_mcp_preload.py -q --tb=short` → **4 failed / 6 passed** with exact mechanism assertions:
1. `test_tool_lane_preloads_mcp_for_child[designer]` — `assert svc.is_preloaded(new_id), "MCP cache never written for the child UUID"` → False
2. `test_tool_lane_preloads_mcp_for_child[planner]` — same mechanism
3. `test_warn_once_on_cache_miss_when_allow_has_mcp` — `assert _miss(caplog) == 1` → `0 == 1` (guard absent at base)
4. `test_warned_set_is_marked_even_when_silent` — `ImportError: _MCP_PRELOAD_MISS_WARNED` (fix-only symbol)

Probe (`tests/unit/probe_designer_od_lane_binding.py`, stdlib script mode): **FAIL (defect present)** — T1 cites 6 bare sync `manager.spawn_instance()` sites with NO preload: `instance.py:2697` (tool lane), `:3009` (spawn_councilor), `:3188`/`:3383` (convene_council ×2), `mappings.py:108` (L3), `thread_manager.py:210` (L6) — exactly the 4 files the fix touches.

**HEAD-state:** 10/10 passed (3.26s) + probe **PASS (all lanes preload)**, T1–T4 green. Logs: `/tmp/odlane-g1-{base,head}-{pytest,probe}.log`.

**FakeManager fidelity: FAITHFUL** — (i) async `ensure_mcp_preloaded` double mirrors real `manager.py:7837`→`:7899-7906` (await in try/except, never raises); ordering + UUID propagation discriminated structurally (fake auto-UUID at `:144-145` ⇒ `preload_calls[0][0] == new_id` only passes via pre-gen → await → sync-spawn-with-id, matching real `instance.py:2778-2792`); CR-3 identity forwarding pinned (`:212` ↔ `manager.py:7900-7904`). (ii) failure paths: missing-service early-return (`:156-157` ↔ `manager.py:7878-7880`, pinned by survives-missing-mcp-service), legit-empty miss-vs-empty gate (`instance.py:363-368`). (iii) warn-once family exercises REAL production symbols (`_load_mcp_tools`, `_warn_once_mcp_preload_miss` `instance.py:329-349`: mark-before-warn, allow-gated, never-raises) — not fake-mediated.
*Nuances (non-blocking):* no test injects a raising fake (contract shape mirrored only); fake lacks repo-row identity fallback (irrelevant on tool lane); `test_loaded_line_fires_at_construction` passes at base by design (seeds cache manually `:220`) — lane-level population is discriminated by the tool-lane tests.

## G2 — No-regression matrix: PASS (a+b+c)

**(a) previously-red-12:** `timeout 300 .venv/bin/python -m pytest tests/test_council_tools.py tests/unit/tools/test_version_tag_tool_resolution.py -q --tb=short` → **47 passed / 0 failed** (28+19, collect-only cross-check sums exact). @0859ee53. Log `/tmp/g2a-pack.log`.

**(b) wider-surface delta:** 142 files (8 named + grep/filename discovery, deduped, e2e/packs/conftest excluded; list recorded once at `/tmp/g2b-files.txt`), 3,958 tests, 11 deterministic chunks, single base-visit flip cycle.
- Raw delta first pass: **10 net-new at HEAD + 4 base-only** (144→150 ids, intersection 140).
- **10 net-new root-caused = stale test double, NOT production regression:** `WalkerManager` in `tests/test_governor_recursion_acceptance_walk.py` (file untouched by the stack) lacked `ensure_mcp_preloaded`; the fix's deliberate council-lane awaits (`instance.py:3287` ×6, `:3100` ×2 — probe T1's own defect sites) hit the AttributeError. Deterministic (isolated re-run 10F/18.8s).
- **Test-architecture quick fix applied (tester authority, test-code-only):** +4/−1 lines, `mgr.ensure_mcp_preloaded = AsyncMock(return_value=None)` mirroring e5af15ca's pattern; file re-run 10F+6P → **16P/0F** (no reds traded); commit **`0859ee53`** `test: add ensure_mcp_preloaded seam to WalkerManager double (council-lane preload)` (local, not pushed).
- **Final literal diff:** net-new at HEAD = **∅**; base-only = exactly the **4 by-design fix-stack seam pins** (council_tools happy-path ×3 in 2 classes + version_tag v2_governor ×1) — these are the G1 discrimination mechanism in stack-EDITED test files (tests held at HEAD while daemon/ flips); adjudicated G1-class, never attributable to the fix.
- Collection-error delta ∅ (0 modules both states). Pre-existing family identity: spawn_limit 9=9, spawn_team_members 18=18, load_skill 7=7 (subset diffs empty); other 140 shared reds identical (governor_integration, progressive_dispatch, test_api, builtin_mcp_servers fixture-ERROR×17, …).
- Runtimes: base ≈8.0 min / head ≈7.9 min across chunks, max chunk 141s ≪ 300s cap, zero timeouts. Lists/logs: `/tmp/g2b-*`.

**(c) related modules:** `timeout 300 .venv/bin/python -m pytest tests/unit/test_mcp_service.py tests/unit/test_mcp_lazy_init.py tests/unit/test_mcp_tool_filter.py tests/unit/test_mcp_cold_load_race.py tests/job_queue/test_job_processor.py tests/test_sources_mapper.py -q --tb=short` → **187 passed / 1 skipped / 0 failed** (3.24s). Log `/tmp/g2c-pack.log`.

## G3 — LIVE-shaped F1 binding verification: PASS

**G3.1 BOOTED ✅** — port **8090** (proved free pre-bind; 8088 never bound/touched), PID **1277011**, `/livez` → 200 `{"status":"alive","version":"0.17.1"}` = worktree build. Fence verified by grep-counts ONLY (no values printed): `POSTGRES_DB=ensemble_dev` ×1, `ensemble_prod` ×0, `ENSEMBLE_SELF_ENV=dev` ×1, nonempty key ×1. Boot deviation documented: dev.sh hardcodes `PORT=8079` (avoid-listed) → uvicorn booted directly replicating dev.sh's exact env chain (fenced .env via `set -a`, SELF_ENV=dev, data_dev dirs, `--timeout-graceful-shutdown 10`), minus `--reload` for single-PID ownership. OD lane warm at boot: opendesign pool 1/1 healthy, all 10 od_* tools adapted; context7 healthy. Zero provider errors. Log `/tmp/odlane-g3-daemon.log`.
Observations (pre-existing, non-gate): plane MCP connect failures ×4; `plane_http_client.py:516` logging-arity TypeError (4 args/3 placeholders) on PlaneSyncWatchdog 409 redrive — cosmetic log corruption, worth a later fix ticket.

**G3.2 agent-lane spawn + MCP binding: ✅ PASS** — Instances: leader `032c621d-6978-4ecc-8aa6-b032ad8bf8d4` (agent leader v1.2.0); designer child `06f4c417-be88-464a-a051-ebd102abc9e7` (parent = leader, spawn log L1133 records `name=odlane-g3-designer-child`).
- **(i) Log ordering PASS** — daemon log lines (all 08:57:38–40Z):
  - L1126 — `Lazy-loaded 12 MCP tool schemas from 3 server(s) for instance 06f4c417`
  - L1128 — `Loaded 12 MCP tools for instance 06f4c417: [mcp_opendesign_od_list_projects, od_get_project, od_create_project, od_update_project, od_delete_project, …]`
  - L1133 — `Spawning instance 06f4c417-… (agent=designer, parent=032c621d-…, name=odlane-g3-designer-child, model=vision, source=llm_model)`
  - L1135 — `[LongToolNudge] TOOL_COMPLETED … tool=spawn_instance duration_ms=1607`
  - **L1128 (Loaded) < L1133 (Spawning) < L1135 (TOOL_COMPLETED)** — preload provably precedes the spawn on the real agent-tool lane.
- **(ii) Bound tool set PASS 12/12** — `GET /api/instances/06f4c417…` `mcp_tool_names` includes all 10 `mcp_opendesign_od_*` (list/get/create/update/delete_project, save/lint_artifact, compose_brief, generate_design, save_project_file) + `mcp_context7_resolve-library-id` + `mcp_context7_query-docs`. None missing.
- Leader turn: 08:57:34Z → 08:57:47Z ≈ 13s, one `spawn_instance` call (1607ms). Zero provider errors. Zero `od_*` tool invocations by the leader — no guard breach.

**G3.3 real od_list_projects + warn-once + teardown: ✅ PASS** —
- **(iii) Real OD exec PASS HEALTHY** — Child's reply: probe **succeeded** ("OD lane available"; "fallback_reason: n/a"; "Cardinal #7 satisfied by construction"). 2 projects returned: `odsp-finale-rerun-login`, `odsp-grand-finale-smoke`. Daemon log (09:05:03Z):
  - L2666 — `[LLM] Tool call: mcp_opendesign_od_list_projects`
  - L2668 — `Transferred pooled session for 'opendesign' to instance 06f4c417`
  - L2669 — `[LongToolNudge] TOOL_COMPLETED … tool=mcp_opendesign_od_list_projects duration_ms=315 threshold_crossed=False`
  Real pooled-session MCP execution, **315ms**. Child turn 09:04:54Z → 09:05:31Z ≈ 37s.
- **Step-0-fires-first (G4 BONUS)**: the child's **first and only** tool call of the turn was `mcp_opendesign_od_list_projects` (`call_1e48a233…`, 09:05:03, `{}` args — matches `Step 0 — Run ONE od_list_projects call at the start of the mockup lane` at workflow.md:72). Whole-session tool-census = exactly 2 (leader spawn + child probe). **Zero `od_generate_design`** — no guard breach.
- **(iv) Warn-once unit evidence PASS 5/5** — at HEAD `0859ee53`, `timeout 300 .venv/bin/python -m pytest tests/unit/test_spawn_lane_mcp_preload.py -q -k "warn" --tb=short` → **5 passed / 5 deselected** in 0.90s: `test_warn_once_on_cache_miss_when_allow_has_mcp`, `test_no_warn_on_legit_empty_cache`, `test_no_warn_when_allow_lacks_mcp`, `test_no_warn_without_mcp_service`, `test_warned_set_is_marked_even_when_silent`. Exactly-once, silent legit-empty, never-raises — all green.
- **Teardown CLEAN** — `DELETE /api/instances/06f4c417…` and `DELETE /api/instances/032c621d…` both → `{"terminated":true}`; post-terminate status both `completed`. Ownership check BEFORE kill: `ps -p 1277011` cmdline = our uvicorn-on-8090 (exact boot command); `ss -ltnp` shows 8090 owner pid=1277011 (our process). SIGTERM via proc handle (exit_code −15); port freed (`ss -ltn` no 8090 listener); process gone; `pgrep -af ensemble-src-wt-odlane-fix` only matched the pgrep shell itself — zero real strays. 8088/8079/7456/5432 untouched throughout.

**Gate record** — Instances created: leader `032c621d…`, designer child `06f4c417…` (both terminated/completed). Daemon PID 1277011, port 8090, lifetime ≈ 17.5 min. Daemon log retained `/tmp/odlane-g3-daemon.log` (4128 lines); worktree data dir `data_dev/` informational. Provider errors across all three messages: **zero**.

## G4 — Prompt/spec gates (Stream 2): PASS 6/6

- **(a)** enum `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` VERBATIM at all 4 canonical sites — rule.md:15 (+ :38), workflow.md:58/:96, skills-template/design-strategy.md:50/:61/:69/:71, templates/design-spec.md:154/:247/:258 (site :149 uses markdown-escaped `\|` table variant — 5 tokens in order; site passes on 3 verbatim matches alone).
- **(b)** template `:146` header carries `mockup_lane` + `fallback_reason`; REQUIRED-when-text (`:154`, `:117`); n/a-when-opendesign (`:260`, example rows :148/:150).
- **(c)** Step-0 probe: workflow.md:70-72 (`Run ONE od_list_projects call at the start of the mockup lane…`) + template mirror :118.
- **(d)** zero text-first residue under agents/designer/ (8 patterns, 0 matches); supplementary sweep found only OD-default/fallback-last language (soul.md:17, design-strategy.md:48, workflow.md:68) — all OK.
- **(e)** design-strategy 1.3.0 lockstep: frontmatter `:2` + skill-set.yaml `:3-4`.
- **(f)** tools_note.md 10/10 od_* documented (inventory counts cited); npm/npx only as anti-pattern warnings (`:80`, `:139`) + package-local idioms; no bare-npx/npm-install-g teaching.

## G5 — Scope fences: PASS

`git diff --name-only c55f2b0a..0859ee53` — 20 files, all classified:
- daemon/ = **exactly** the allowed 4: `tools/instance.py`, `services/mcp_service.py`, `routers/mappings.py`, `sources/adapters/slack/thread_manager.py`
- tests/ = 8 files (incl. the two pinned gate files + WalkerManager seam file)
- agents/ = designer prompt stream only (6 files: soul/rule/workflow/tools_note/skill-set.yaml/skills-template/design-strategy); zero agents outside designer/
- frontend/ = 0 · `**/meta.json` = 0
- .agents/shared/planning/ = 2 files (decision record + template — expected)
- Stack shape: 5 commits (4 fix + 1 test seam), `rev-list --count` = 5 ✓
- ensure.md rider: dev.sh:142 `--timeout-graceful-shutdown 10` ✓
- Worktree scratch: only `.agents/shared/planning/designer-agent/install-audit.jsonl` (planning artifact, expected)

---

## Quick fixes applied this run

1. **WalkerManager MCP-preload seam** — `tests/test_governor_recursion_acceptance_walk.py` +4/−1 (test-code only). Root cause: e5af15ca's AsyncMock sweep covered 3 manager constructions but missed this empty-inline-harness class (not a MagicMock() construction — grep-invisible). Fix mirrors the e5af15ca pattern against the real signature. Verified 10F→16P/0F, no reds traded. Commit `0859ee53` (local). LESSONS entry: `2026-10-06-walkermanager-seam-sweep-gap.md`.

## Observations (non-gate, for the record)

- `plane_http_client.py:516` logging-arity bug (pre-existing; TypeError inside logging on 409-conflict warning path). Suggest a small fix ticket — outside this commission's fence.
- `test_loaded_line_fires_at_construction` passes at base by seeding the cache — module docstring's base-failure prediction for that one test is inaccurate; harmless.
- dev.sh has no PORT knob (unconditional 8079) — G3 documented a uvicorn-direct boot replicating the env chain; if multi-daemon dev gates recur, a PORT-respecting dev.sh would remove the deviation.
- G2b first-pass caught a real test-double drift the review streams missed — the wider-surface delta gate earned its keep.

## Gaps

None. All 7 gates delivered with command + observed evidence; every dispatched pack reported (zero re-dispatches needed, zero incomplete nodes).

## ensure.md status (scoped to change set)

- Core/Changed-packs regression: ✅ (G1 + G2a/b/c evidence)
- Core/`dev.sh --timeout-graceful-shutdown 10`: ✅ (G5 rider, dev.sh:142)
- Release Gate (full non-integration suite + E2E): NOT triggered — blast radius is a scoped lane fix (4 daemon files + prompt stream), not a big/critical/architecture change; the commission's gate set (incl. live-shaped G3) supersedes in coverage of the changed seam.

## Overall verdict

# 🟢 SHIP — 7/7 gates PASS at `0859ee53`

The designer-od-lane-fix stack (`666c6483→037adb83→e5af15ca→b1e32252` + tester test-only seam commit `0859ee53`) is cleared for version staging + merge:

1. **Discrimination proven** — pinned tests fail at base with the exact mechanism, pass 10/10 at HEAD; probe censuses all 6 previously-bare spawn sites now preloading.
2. **Zero regression** — 142-file/3,958-test wider surface: net-new at HEAD = ∅ after one test-double seam repair; pre-existing red families byte-identical; 12 previously-red green; 6 related modules 187P/0F.
3. **The F1 gap is closed LIVE** — real daemon, real agent-tool lane, preload-before-spawn ordering proven in logs, 12/12 dev-lane MCP tools bound on the designer child, real 315ms `od_list_projects` with Step-0 firing first, clean teardown.
4. **Prompt stream fully reconciled** — enum verbatim, template discipline, zero text-first residue, skill 1.3.0 lockstep.
5. **Scope fences hold** — daemon/ = exactly the 4 allowed files; zero frontend/meta.json/out-of-scope agent changes.

Ride-along notes for the commissioner (non-blocking): plane_http_client.py:516 logging-arity bug (pre-existing); child `instance_name` reads None via detail API vs spawn-log name (API field candidate); dev.sh lacks a PORT knob (G3 documented the uvicorn-direct alternative); `test_loaded_line_fires_at_construction` docstring overstates its base-failure prediction. Post-promote live smoke (incl. od_generate_design e2e) remains a separate commission per plan.
