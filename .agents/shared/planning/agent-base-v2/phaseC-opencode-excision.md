# Phase C — Opencode Service Excision (agent-base-v2 migration)

Owner scope: **Phase C** of the agent-base-v2 mission. Excises the opencode service from the daemon source tree, after Phase A's `legacy/` archive and Phase B's `base := v2` copy have been verified. C does NOT edit agent dirs (those became v2 in Phase B), docs (those are Phase D), or commit messages promoting the work (those are decided later). Siblings: `phaseA-legacy-archive.md`, `phaseB-base-copy.md` (predecessors — read first to confirm archive + copy are verified); `phaseD-references-docs.md` (parallel — comment-only code sites are MINE; user-facing docs are D's); `phaseE-verification-evidence.md` (final evidence pack consumes the boot proof this phase produces).

## 1. Objective

**Full-stack framing (binding — supersedes §1's earlier "opencode service" framing).** Remove the **entire opencode stack** (all related modules in `ensemble` and back up to `legacy/`) from active use: the opencode comm-service lane + the opencode session/API coding-worker integration lane + shared modules in `daemon/opencode/` + the OpenCode resources the `InstanceManager` coordinates + MCP configs referencing opencode (if any — read-only recon pre-addendum §13.2) + the agent-facing surface (the `innate_skills: ["opencode"]` category in `daemon/tools/instance.py`) + every other surface the exploration sweep identifies. ALL of it is removed from the ensemble's active codebase and backed up to the same `legacy/` dir (copy-then-verify, preserved but unused) by Phase A; this phase severs and removes from active use.

**Branch point (binding — see §13).** The unit of work is gated by a LIVE-DEPENDENCY VERDICT step (§5 Step 0.5 / §13.3):

- **BRANCH 1 (expected default):** full-stack removal — sever everything; archive already lives in `legacy/` per Phase A. Expected because the opencode stack is dead/never-invoked in the post-Phase-B state (preliminary evidence §13.2).
- **BRANCH 2 (isolated exception):** if any surface the verdict marks live-used — KEEP that module in place, sever everything around it, FLAG as USER DECISION ITEM (options: re-point to the native system vs retire the agent). Never silently resolved.

**Post-condition (under either branch, ensemble's own system must be unregressed):**

> After Phase C is merged on `feature/agent-base-v2`, `daemon/` no longer imports `daemon.opencode`, `daemon.tools.external_opencode`, or any other opencode-related module from anywhere outside `legacy/`, no MCP config preset wires an opencode-named server, no agent's `innate_skills` array references `["opencode", ...]` (post-Phase-B base), the `Makefile` no longer backs up `opencode_sessions.db`, the daemon boots clean in the worktree's fenced dev lane with no opencode-related log lines or tracebacks, and the test suite passes after the opencode mocks are stripped from shared tests and the opencode-specific tests have moved to `legacy/tests/`. The boot-clean invariant under either branch is the headline constraint (risk #1).

## 2. Scope

### In scope — files this phase MAY edit / move

**Source-tree removal (move-after-archive; Phase A must already have placed copies in `legacy/daemon/`, `legacy/daemon/tools/`, `legacy/agents/_prompt_system/innate-skills/`, and confirmed sha-checksums before this phase starts):**

- `daemon/opencode/{__init__, client, constants, registry, repository, server, session_manager, state}.py` (8 modules; verified at `ls daemon/opencode/`).
- `daemon/tools/external_opencode.py` (1 file; the 8 tool factories are inside).
- `agents/_prompt_system/innate-skills/opencode/skill.md` + `skill.md.bak` (2 files; verified at `ls agents/_prompt_system/innate-skills/opencode/`).

**Test + bug-doc + test-pack archival (move to `legacy/` mirroring source shape per Phase A layout):**

- `tests/opencode/{__init__, conftest, test_client, test_error_propagation_edge_cases, test_integration, test_registry, test_repository, test_server, test_session_manager, test_state, test_table_creation, test_tools}.py` (12 files).
- `tests/test_innate_skills_refactoring.py` — KEEPS IN-TREE; edited in place in C.4 (P1 — uses "opencode" as the canonical skill-loading example; works with `tmp_path` fixtures, does not depend on the real `agents/_prompt_system/innate-skills/opencode/skill.md`).
- `tests/migration/test_jsonb_migration.py` — KEEPS IN-TREE; edited in place in C.4 (R2 — module-level `OpenCodeSessionRecord` import at `:75` + 2 `(opencode_sessions, …)` pair rows + the `assert len == 17` counter + the embedded `_DO_BLOCK_SQL` mirror at `:366-367` all need C.4 edits; whole-file move would silently delete a live test that 15+ callers depend on).
- `docs/bugs/external-opencode-wait-latency.md`, `docs/bugs/opencode-session-manager-memory-leak.md` (2 bug docs).
- `test/packs/opencode_native_tools_unit_test.sh`, `test/packs/ab/top_opencode_unit_test.sh`, `test/packs/regression_opencode_e2e_test.sh` (3 test-pack shell scripts).
- `example/SKILL.md` (DECISION GATE — see §6 below; verify with phaseA whether Phase A moved this; if still present, decide delete-or-archive at C's planning boundary).

**Test mock STRIPS — files this phase edits ONLY to remove dead-mock lines (no semantic change, no import additions):**

- `tests/test_council_tools.py:55` — drop the `patch("daemon.tools.instance.create_opencode_tools", ...)` line + the now-unused `from unittest.mock import patch` if applicable.
- `tests/test_governor_recursion_acceptance_walk.py:336` — drop the patch line.
- `tests/test_manager_shutdown.py:126` — drop the `manager._shutdown_opencode_registry = AsyncMock()` line (the method no longer exists post-Phase-C; leaving the setattr raises `AttributeError` at test setup).
- `tests/tools/test_send_message_load_skill.py:77` — drop the patch line.
- `tests/tools/test_send_message_context_param.py:106` — drop the patch line.
- `tests/tools/test_auto_kill_integration.py:992` — drop the `manager._shutdown_opencode_registry = AsyncMock()` line.
- `tests/test_governor_integration.py:61, 955` — drop both patch lines.
- `tests/helpers/send_message_fixtures.py:68` — drop the patch line.
- `tests/integration/test_spawn_default_unchanged.py:163` — drop the patch line.
- `tests/unit/test_governor_recursion_guard.py:170` — drop the patch line.
- `tests/unit/tools/test_version_tag_tool_resolution.py:432, 1104, 1292` — drop all three patch lines.
- `tests/unit/tools/test_spawn_councilor_default_version.py:126` — drop the patch line.
- `tests/test_spawn_team_members.py:58` — drop the patch line.
- `test/packs/wc_wake_off_bytecompat_probe_test.py:293` — drop the patch line.
- `tests/unit/test_coder_agent.py:40-44` — drop the `"external_opencode": [...]` entry from the test's expected-tool-set fixture.
- `tests/test_spawn_instance_instructive_errors.py:26,42,50,362,368` — verify the `"opencode"` string is used as a SKILL-NAME fixture inside `@skip`'d tests; if the surrounding test bodies are unconditionally skipped, the strings are dead and may be replaced with any string; otherwise leave the strings alone (they're legitimate "skill vs agent" fixtures independent of the opencode service). Confirm at execution time by reading the test bodies around the cites.

**Boot-critical code edits (Tier-1; grep-verify at use time):**

- `daemon/manager.py` — strip :78 import (`from .opencode import OpenCodeSessionRegistry, create_opencode_session_repository`), :1420-1436 init block (incl. `opencode_db_path` + `self._opencode_engine` + `self._opencode_session_repository` + `self._opencode_registry` + the `logger.info` line), :2902-2910 `opencode_registry` accessor method, :3325-3338 already-commented recovery site (verify it is commented at use time — if it ever uncomments, strip both the call and the surrounding `try/except`), :5948-5956 idle-eviction block in the periodic-maintenance loop, :6750-6751 `'opencode_sessions', 'latest_response'` and `'opencode_sessions', 'questions'` schema DO-block rows, :13033 (`("shutdown_opencode_registry", self._shutdown_opencode_registry())` line in the shutdown tuple), :13078-13103 (`_shutdown_opencode_registry` method body — REMOVE both the call site in step :13033 AND the method definition :13078-13103 together).
- `daemon/tools/instance.py` — strip :155-167 (`"opencode": ["external_opencode"],` entry from `INNATE_SKILL_TOOL_CATEGORIES` dict — DO NOT touch the other entries), :259 (`from .external_opencode import create_opencode_tools` line), :5815-5818 (the `opencode_tool_list = create_opencode_tools(manager, current_instance_id)` + `tools.extend(opencode_tool_list)` pair, plus the surrounding comment header at :5816). Verify sites drift — `grep "create_opencode_tools" daemon/tools/instance.py` at execution time and edit the cited lines.
- `daemon/tools/_tool_registry.py` — strip :603 (`"external_opencode": "daemon.tools.external_opencode",` entry in the module-map dict), :764-771 (the 8 `external_opencode_*` string entries from the `KNOWN_TOOL_NAMES` validation list — DO NOT touch the surrounding entries). The frozen-binary validation list MUST stay consistent: removing all 8 imports and the module map while leaving the 8 strings in `KNOWN_TOOL_NAMES` would cause valid Python imports to be rejected as unknown tool names; leaving the strings in place after import-removal would cause every opencode tool call in legacy/test to fail with "unknown tool". Removal of all 3 sites (import, factory, KNOWN_TOOL_NAMES row, module-map row) MUST land in the same commit.

**Comment-only cleanups (Tier-2; non-boot-critical, may land in a separate cleanup commit from Tier-1):**

- `daemon/config.py:168` — remove the word "opencode" from the `model_keywords` field description (the field still serves `daemon.services.keyword_extraction` — keep the field, drop the daemon-name reference). Replaced phrasing: "Model for keyword extraction from outgoing prompts" (drop "(falls back to model). Set to 'quick' …" body if you want, body is field-behavior description independent of opencode).
- `daemon/services/context_injection.py:541, 846` — rephrase the comment to drop the `external_opencode.py` reference at :541 and the "opencode via MCP" analogy at :846.
- `daemon/services/context_messages.py:35, 39` — drop the ADR-13 comment block (or annotate as ADR-retired-by-Phase-C).
- `daemon/services/critical_note_gate.py:8` — drop the `daemon/tools/external_opencode.py` reference from the module docstring's "related modules" list.
- `daemon/services/waiting_children_watchdog.py:9, 752` — rephrase the `OpenCode subprocess` analogy in the module docstring + the comment near line :752 (`OpenCode subprocess activity`).
- `daemon/loader.py:226` — rephrase the comment example that names `(e.g. "opencode")`.
- `daemon/repositories/task/repository.py:1777` — DO NOT EDIT CONTENT (D5: `keyword_extraction` STAYS). The comment text mentioning "OpenCode subprocess activity" sits inside a heartbeat-predicate rationale block; you MAY rephrase it to drop the daemon-name analogy, but if the executor isn't sure, LEAVE the comment alone — D5 says the keyword_extraction surrounding code may not churn.

**Build/manifest edits:**

- `Makefile:173-174, :186` (R5) — rephrase the `:173-174` comment to drop the "opencode_sessions.db (always a local SQLite file, never migrated to postgres)" wording AND drop the `opencode_sessions.db` + WAL/SHM siblings from `:186` FILES=

### Out of scope — explicitly NOT touched by this phase

- **Runtime DBs `data*/opencode_sessions.db`** (D7) — boot-recreated state, never deleted by C. Phase A/B/C commit work does NOT touch `data*/`; the worktree's dev DATA_DIR lives at `data/` (verified absent in the worktree; will be created at first boot via `__table__.create(checkfirst=True)` per `daemon/opencode/repository.py:313`. After C, the engine init in `manager.py:1420-1436` is gone, so the `__table__.create` no longer fires — the engine never starts — and a stale `data/opencode_sessions.db` is harmlessly orphaned. Per D7: orphaned runtime DBs are NOT cleaned up by C. A follow-up mission may garbage-collect them; not C's job.
- **`daemon/services/keyword_extraction.py`** (D5 explicit) — module STAYS in the daemon; its 5 string-mention "opencode" touches (module docstring line 1, docstring line 9, log lines :426 and :430) are by-design; C does NOT touch the module. The function is reused by `daemon/services/attestation_report_judge.py` and the `daemon/graph.py:3842, 3847` keyword-extraction call sites, which are all opencode-independent.
- **`daemon/plugins/opendesign/copy_freely/design-systems/opencode-ai/`** — vendored third-party design-system resource (not our service); false positive.
- **`docs/mcp-kb-usage.md`** (D6 explicit) — references the upstream `sst/opencode` product, NOT our opencode service. UNTOUCHED.
- **`.agents/tester/RESULTS/*` / `.agents/tester/LESSONS/*` / `.agents/*/RESULTS|LESSONS` historical records** (Tier-5 per task binding) — LEAVE in place. DO NOT touch, even if they reference opencode-era filenames.
- **`example/SKILL.md`** unless Phase A's archive plan (`phaseA-legacy-archive.md`) explicitly addressed it — let Phase A's worker own this judgment. If Phase A archived it, C has no work. If Phase A left it in the tree, C decides between git-rm and git-mv-to-legacy/.
- **Frontend `frontend/src/app/pages/jobs/jobs.component.ts:2389`** (note-only per binding instructions) — verification target only. NO edit.
- **`tests/test_spawn_instance_instructive_errors.py:26,42,50,362,368`** — depends on `@pytest.mark.skip(reason="Instructive error messages not yet implemented")` being asserted at execution time. If still skipped, strings are dead (no harm if left); if un-skipped, the executor MUST verify the test does not depend on the opencode factory (it uses "opencode" as an INNATE-SKILL NAME, not the service — but losing the `agents/_prompt_system/innate-skills/opencode/skill.md` file would still break the test's skill discovery path).
- **`docs/test-suite-known-issues.md:43`, `docs/skill-evolution.md`, `docs/plans/*`, `docs/optimization/*`, `docs/mcp-integration.md`, `docs/agents.md` body, `docs/bugs/agent-call-wrong-tool-spawn-session.md`, `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md`, `LESSONS/architecture-migration-status-2026-06-26.md`, `backup/agents/*`, `agents/devops/soul.md`, `agents/maintenancer/knowledge/*`, `.slim/cartography.json`** — historical/stale references Phase D or a follow-up cleans up; C does NOT edit these.
- **`TOOL_REQUIRED_AGENTS` (verify-only, no entry exists for "opencode" in `daemon/tools/_auth.py:35-51`)**, **`REPORT_REPAIR_EXCLUDED_AGENTS` (verify-only, no "opencode" entry in `daemon/constants.py:1086-1088`)**, **`default_agent_versions` (no "opencode" entry in `daemon/constants.py:411`)**, **attestation leader refs in `daemon/services/instance_lifecycle.py:2305,4753`**, **spawn-by-name pins**, **default `"ari"` ×5 pins**, **`frontend/.../jobs.component.ts:2389`** — all name pins verified safe (no opencode tokens to churn). DO NOT EDIT.

### Edit-authority boundaries

- **Boot-critical daemon wiring (Tier-1)** + **source-tree move-after-archive** = Phase C.
- **Test mock STRIPS from shared tests** + **test-file MOVES to legacy** + **code comments referencing opencode** = Phase C.
- **User-facing docs (`docs/*`, `README.md`, `AGENTS.md`, `CHANGELOG.md`)** = Phase D. C does not edit these even when they reference opencode.
- **Agent dirs (`agents/<name>/`)** = Phase B already converted to v2; C does NOT touch.
- **`legacy/` archive** = Phase A already created + verified; C moves source files into the existing archive mirror paths (NOT creating new archive layout).
- **Build/manifest** = Phase C (Makefile FILES list).
- **Frontend** = verify-safe only (no edits in any phase).

## 3. Dependencies & Ordering

| Position | Phase | Produces what Phase C needs |
|---|---|---|
| Before C | **Phase A (legacy archive)** | `legacy/daemon/opencode/`, `legacy/daemon/tools/external_opencode.py`, `legacy/agents/_prompt_system/innate-skills/opencode/`, `legacy/tests/opencode/`, `legacy/docs/bugs/external-opencode-wait-latency.md`, `legacy/docs/bugs/opencode-session-manager-memory-leak.md`, `legacy/test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}` all present with sha-verified byte-equality to the originals. **Phase A's move-set is the stack-only (STACK-1..11 per `phaseA-legacy-archive.md`): it NEVER produces `legacy/tests/test_innate_skills_refactoring.py` or `legacy/tests/migration/test_jsonb_migration.py` — those two shared test files stay IN-TREE and are edited in place in C.4 per §13.5.** Phase C MUST verify this prerequisite before any removal: re-grep `legacy/daemon/opencode/` exists with sha equal to `daemon/opencode/`. If sha mismatches: STOP and ask Phase A to re-verify (per Cardinal #2 of "do not silently damage shared state"). |
| Before C | **Phase B (base copy)** | `agents/{developer,reviewer,tidier,approver,planner}/` (R7: leader is NOT in the set; `agents/leader[v2]` does not exist) are byte-identical copies of `agents/{developer,reviewer,tidier,approver,planner}[v2]/`. Phase C does NOT depend on Phase B's outcome for code-correctness (C doesn't touch agent dirs), but boot success DOES depend on Phase B having removed `innate_skills: ["opencode"]` from base agent `meta.json` files BEFORE C strips the `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` entry (because if Phase B hasn't run, base agents with `["opencode", ...]` meta.json will try to resolve an innate skill whose map entry has been removed — graceful fallback at `daemon/tools/instance.py:171+` `.get` default; warning at `daemon/loader.py:389` for missing prompt dir; architect §3.6 confirmed both paths safe). Phase B verification = `diff -r agents/developer agents/'developer[v2]'` (and the other 4 pairs) returns no differences. |
| Independent of C | **Phase D (docs refresh)** | Edit user-facing docs post-Phase-C so the docs describe post-Phase-C reality. C runs in any order relative to D, but the BOOT PROOF C emits is consumed by Phase E (and D may want to read C's boot proof for its consistency-sweep table). |
| After C | **Phase E (verification evidence)** | Reads C's boot proof + final grep sweep + the 3-commit C history + the test-suite-green log. C hands off by committing the boot-proof artifact to `.agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md`. |

**Hard ordering invariant for C:** Phase A → Phase B → Phase C. Phase A must be complete (proven via sha equality) BEFORE Phase C begins any move. Phase B must be complete (proven via diff) BEFORE Phase C strips `INNATE_SKILL_TOOL_CATEGORIES["opencode"]`.

## 4. File Inventory (exact paths)

### 4.1 Source files REMOVED from working tree (archive-mirrored copies live in `legacy/`)

| Working-tree path | Count | Post-state |
|---|---|---|
| `daemon/opencode/__init__.py` | 1 | file gone |
| `daemon/opencode/client.py` | 1 | file gone |
| `daemon/opencode/constants.py` | 1 | file gone |
| `daemon/opencode/registry.py` | 1 | file gone |
| `daemon/opencode/repository.py` | 1 | file gone |
| `daemon/opencode/server.py` | 1 | file gone |
| `daemon/opencode/session_manager.py` | 1 | file gone |
| `daemon/opencode/state.py` | 1 | file gone |
| `daemon/tools/external_opencode.py` | 1 | file gone |
| `agents/_prompt_system/innate-skills/opencode/skill.md` | 1 | file gone |
| `agents/_prompt_system/innate-skills/opencode/skill.md.bak` | 1 | file gone |

Total: 11 files deleted from working tree; each has a corresponding `legacy/` mirror copy preserved by Phase A.

### 4.2 Test files MOVED to `legacy/` (mirror-shape)

| Working-tree path (PRE) | Post-state |
|---|---|
| `tests/opencode/` (12 files; verify exact list at execution time via `ls tests/opencode/`) | `legacy/tests/opencode/` (12 files) |
| `test/packs/opencode_native_tools_unit_test.sh` | `legacy/test/packs/` |
| `test/packs/ab/top_opencode_unit_test.sh` | `legacy/test/packs/ab/` |
| `test/packs/regression_opencode_e2e_test.sh` | `legacy/test/packs/` |

> **Note (C-1 fix):** `tests/test_innate_skills_refactoring.py` and `tests/migration/test_jsonb_migration.py` are NOT in this MOVE table. They stay IN-TREE and are edited in place in C.4 (see §13.5 TOUCH set rows + §13.7 C.4 commit). This corrects an earlier-draft contradiction where these two files appeared in both MOVE and TOUCH.

### 4.3 Bug docs MOVED to `legacy/docs/bugs/`

| Working-tree path (PRE) | Post-state |
|---|---|
| `docs/bugs/external-opencode-wait-latency.md` | `legacy/docs/bugs/` |
| `docs/bugs/opencode-session-manager-memory-leak.md` | `legacy/docs/bugs/` |

### 4.4 Tier-1 BOOT-CRITICAL file edits (grep-verify at use time; sites drift)

| File | Cites (verify at execution time) | Edits |
|---|---|---|
| `daemon/manager.py` | :78, :1420-1436, :2902-2910, :3325-3338, :5948-5956, :6750-6751, :13033, :13078-13103 | Strip the 8 site-groups; preserve all surrounding code; remove both the call site at :13033 AND the method definition at :13078-13103 together |
| `daemon/tools/instance.py` | :155-167, :259, :5815-5818 | Drop `"opencode": ["external_opencode"],` row from `INNATE_SKILL_TOOL_CATEGORIES`; drop the `from .external_opencode import create_opencode_tools` line; drop the `opencode_tool_list = create_opencode_tools(manager, current_instance_id)` + `tools.extend(opencode_tool_list)` pair |
| `daemon/tools/_tool_registry.py` | :603, :764-771 | Drop the `"external_opencode": "daemon.tools.external_opencode",` row from the module-map; drop the 8 `external_opencode_*` rows from `KNOWN_TOOL_NAMES` |
| `Makefile` | :173-174, :186 (R5) | Rephrase the `:173-174` comment to drop the "opencode_sessions.db (always a local SQLite file, never migrated to postgres)" wording AND drop the `opencode_sessions.db` + WAL/SHM siblings from `:186` FILES= |

### 4.5 Tier-2 comment-only file edits (cosmetic; same-boot-invariants)

| File | Cites | Edits |
|---|---|---|
| `daemon/config.py` | :168 | Rephrase `model_keywords` field description to drop "opencode" reference (replace "outgoing opencode prompts" with "outgoing prompts"); the field stays |
| `daemon/services/context_injection.py` | :541, :846 | Drop `external_opencode.py` reference; drop "opencode via MCP" analogy |
| `daemon/services/context_messages.py` | :35, :39 | Drop or annotate ADR-13 comment block |
| `daemon/services/critical_note_gate.py` | :8 | Drop `daemon/tools/external_opencode.py` from the module docstring's "related modules" list |
| `daemon/services/waiting_children_watchdog.py` | :9, :752 | Drop `OpenCode subprocess` analogy |
| `daemon/loader.py` | :226 | Drop `"opencode"` from the example comment; replace with the more generic `"question"` or `"chart"` (a real v2-base innate skill example) |
| `daemon/repositories/task/repository.py` | :1777 | LEAVE (D5 ambiguity — comment-only sits inside a heartbeat-predicate rationale; rewriting is non-essential AND D5 says keyword_extraction surrounding code "may not churn") |

### 4.6 Tier-4 test mock STRIPS (file edits; no semantic change, only dead-mock removal)

| File | Cites | Edits |
|---|---|---|
| `tests/test_council_tools.py` | :55 | Drop `patch("daemon.tools.instance.create_opencode_tools", return_value=[])` |
| `tests/test_governor_recursion_acceptance_walk.py` | :336 | Same |
| `tests/test_manager_shutdown.py` | :126 | Drop `manager._shutdown_opencode_registry = AsyncMock()` |
| `tests/tools/test_send_message_load_skill.py` | :77 | Drop patch line |
| `tests/tools/test_send_message_context_param.py` | :106 | Drop patch line |
| `tests/tools/test_auto_kill_integration.py` | :992 | Drop `manager._shutdown_opencode_registry = AsyncMock()` |
| `tests/test_governor_integration.py` | :61, :955 | Drop both patch lines |
| `tests/helpers/send_message_fixtures.py` | :68 | Drop patch line |
| `tests/integration/test_spawn_default_unchanged.py` | :163 | Drop patch line |
| `tests/unit/test_governor_recursion_guard.py` | :170 | Drop patch line |
| `tests/unit/tools/test_version_tag_tool_resolution.py` | :432, :1104, :1292 | Drop all three patch lines |
| `tests/unit/tools/test_spawn_councilor_default_version.py` | :126 | Drop patch line |
| `tests/test_spawn_team_members.py` | :58 | Drop patch line |
| `test/packs/wc_wake_off_bytecompat_probe_test.py` | :293 | Drop patch line |
| `tests/unit/test_coder_agent.py` | :40-44 | Drop the `"external_opencode": [list]` entry from the test's expected-tool-set fixture |

### 4.7 Evidence artifact CREATED by this phase

| Path | Content |
|---|---|
| `.agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md` | Fenced dev-lane boot transcript (worktree claim ENSEMBLE_SELF_ENV=dev, ensemble_dev DB, PORT 8079); livez/readyz PASS lines; grep -v opencode log scan result; shutdown traceback-free evidence; final grep sweep proof |

## 5. Step-by-step Operations (commit-checkpoint ordering; bootable at every checkpoint)

> **C-1 + N2 note (added by fix passes):** This section pre-dates the §13 addendum-era design. Where §5 conflicts with §13, **§13 is authoritative for the entire commit-split AND the test-file move-vs-edit decision.** Concretely, §13 supersedes §5 on:
> - **C.1–C.5 commit split** (executor MUST follow §13.7's sequence: C.1 Tier-1 wiring severance → Step 1.5 BLOCKING GATE → C.2 stack-removal (`git rm`-only) → C.3 bulk move-to-legacy → C.4 in-place TOUCH edits → C.5 boot-proof artifact). The older §5 Steps 1-4 below describe a pre-addendum 4-step split that is NOT authoritative; only the per-step intents (sha-equality, wiring severance, file moves, test edits, boot proof) survive as a reference, not as the operational sequence.
> - **Per-commit invariants (R6)** — C.1 boot-green only; C.2 boot-green only; C.3 move-only, no test-green gate; **C.4 = FIRST test-green commit**. The older §5 "bootable at every checkpoint" framing is preserved at the **boot** layer (still true), but the test-green layer is now explicitly deferred to C.4 (was implicit).
> - **Test-file move-vs-edit decision** — `tests/test_innate_skills_refactoring.py` and `tests/migration/test_jsonb_migration.py` are NOT moved; they stay IN-TREE and are edited in place in C.4 per §13.5. The C-1 fix-pass correction in this section removed the two `git mv` lines from Step 3 + commit-message rows; those two files are now in §13.5 TOUCH set (C.4 in-place edits).
> - **Commit-message templates** — use the §13.7 `[agent-base-v2/C/N]` prefixes, not the older §5 prefixes.
>
> The remaining prose in §5 (the "boot-clean invariant" lead-in, the Step 0 sha/diff preflight, the per-step verification shell snippets, the DoD cross-references) is reference material — informative, not operational. When in doubt, follow §13.

The boot-clean invariant is the headline constraint: at every intermediate commit boundary, the daemon must boot without ImportError, AttributeError, or "unknown tool" validation errors. The risk #1 mitigation strategy is **wire-then-delete sequencing**: Tier-1 wiring removal lands in the same commit as the source-tree removal, so no commit imports a removed module and no commit leaves a dangling reference.

### Step 0 — Pre-flight (no commit; verification only)

```
cd /home/nea/ensemble-src-wt-agent-base-v2

# Verify Phase A archive complete
ls legacy/daemon/opencode/ 8_file_check  # must list __init__, client, constants, …
sha256sum legacy/daemon/opencode/*.py daemon/opencode/*.py | sort  # byte-identity required
sha256sum legacy/daemon/tools/external_opencode.py daemon/tools/external_opencode.py
sha256sum legacy/agents/_prompt_system/innate-skills/opencode/skill.md agents/_prompt_system/innate-skills/opencode/skill.md
sha256sum legacy/agents/_prompt_system/innate-skills/opencode/skill.md.bak agents/_prompt_system/innate-skills/opencode/skill.md.bak
# If any sha mismatches: STOP. Phase A is not verified. Report to dispatcher.

# Verify Phase B base copy complete
diff -r agents/developer agents/'developer[v2]'  # must be empty
diff -r agents/reviewer agents/'reviewer[v2]'    # must be empty
diff -r agents/tidier agents/'tidier[v2]'        # must be empty
diff -r agents/approver agents/'approver[v2]'    # must be empty
diff -r agents/planner agents/'planner[v2]'      # must be empty
# NB: leader is NOT in the migration set (R7); agents/leader[v2] does not exist
# If any diff non-empty: STOP. Phase B is not verified. Report to dispatcher.

# Establish dev-lane env (fence; no live contact)
cat > /tmp/c0_env.sh <<'EOF'
export ENSEMBLE_SELF_ENV=dev
export ENSEMBLE_POSTGRES_DB=ensemble_dev
export ENSEMBLE_PORT=8079
# Verify dev.sh guard exact name + the data_dir it points to at execution time.
EOF
```

### Step 1 — Commit C.1: TIER-1 WIRING REMOVAL (boot must succeed AFTER this commit)

**This commit lands the most dangerous change in isolation, so a boot failure is exactly this commit's fault and not entangled with file-removal noise.**

Operations:
- `daemon/manager.py` — apply the 8 site-group edits (§4.4 cites; verify lines at use time).
- `daemon/tools/instance.py` — apply the 3 site-group edits (drop `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` row, drop the import, drop the factory call + tools.extend + comment header).
- `daemon/tools/_tool_registry.py` — apply the 2 site-group edits (drop module-map row, drop the 8 `KNOWN_TOOL_NAMES` rows).
- **No file moves in this commit.** `daemon/opencode/`, `daemon/tools/external_opencode.py`, and the prompt files remain on disk (now orphaned — no live import wires them in).

Verification before committing:

```
# Static check: no daemon code references the removed symbols
grep -rn "from .opencode import\|from daemon.opencode" daemon/ 2>&1
grep -rn "from .external_opencode import\|from daemon.tools.external_opencode" daemon/ 2>&1
grep -rn "create_opencode_tools\|create_opencode_session_repository\|OpenCodeSessionRegistry" daemon/ 2>&1
# All four queries must return ZERO matches outside daemon/opencode/ and daemon/tools/external_opencode.py themselves

# Static check: KNOWN_TOOL_NAMES consistent
grep -n "external_opencode" daemon/tools/_tool_registry.py
# Must return ZERO matches (module-map row gone AND KNOWN_TOOL_NAMES rows gone)
```

Boot proof (fenced dev lane only — DO NOT boot the main checkout):

```
ENSEMBLE_SELF_ENV=dev PORT=8079 ./dev.sh boot   # verify exact invocation at use time
# OR, if the launcher script differs, use run_app.py directly with the worktree's env vars.
# Wait for /livez and /readyz to return 200.
# Grep log for any line containing "opencode" — must return ZERO matches.
curl -s http://localhost:8079/livez | jq .
curl -s http://localhost:8079/readyz | jq .
kill -TERM <pid> # wait for clean shutdown; tail log to confirm no traceback
```

Commit message:

```
[agent-base-v2/C] chore(daemon): tier-1 opencode wiring removal

Strip daemon/manager.py + daemon/tools/instance.py + daemon/tools/_tool_registry.py
references to the opencode service. The service's modules and tools stay on disk
in this commit (removed in C.2) to keep the diff single-responsibility and the
boot-clean invariant local to this change.

Sites (grep-verify; lines may have drifted):
- manager.py:78, 1420-1436, 2902-2910, 3325-3338, 5948-5956, 6750-6751, 13033, 13078-13103
- instance.py:155-167, 259, 5815-5818
- _tool_registry.py:603, 764-771

Boot verified: fenced dev lane (ENSEMBLE_SELF_ENV=dev, ensemble_dev DB, PORT 8079).
[add livez/readyz lines + log scan grep -c result]
```

### Step 2 — Commit C.2: SOURCE-TREE REMOVAL (boot must succeed AFTER this commit)

**This commit deletes the orphaned modules from disk. Step 1 already removed all import wires, so this commit's removals must not surface any new import error.**

Operations:
- `git rm daemon/opencode/__init__.py daemon/opencode/client.py daemon/opencode/constants.py daemon/opencode/registry.py daemon/opencode/repository.py daemon/opencode/server.py daemon/opencode/session_manager.py daemon/opencode/state.py`
- `git rm daemon/tools/external_opencode.py`
- `git rm agents/_prompt_system/innate-skills/opencode/skill.md agents/_prompt_system/innate-skills/opencode/skill.md.bak`

Verification before committing:

```
# Working tree has zero opencode implementation files
find daemon/ agents/_prompt_system/ -name '*opencode*' -o -name '*OpenCode*' 2>&1
# Must return ZERO matches

# Phase A archive is intact
ls legacy/daemon/opencode/ legacy/daemon/tools/external_opencode.py legacy/agents/_prompt_system/innate-skills/opencode/
# Both listings must still exist with full content

# Booted daemon (in dev lane) before this commit still responds; after this commit it should respond with zero opencode noise
curl -s http://localhost:8079/livez | jq .
```

Boot proof (same fence; re-run):

```
# Re-boot using the same env
ENSEMBLE_SELF_ENV=dev PORT=8079 ./dev.sh boot
curl -s http://localhost:8079/livez | jq .
grep -i opencode data/logs/*.log 2>&1 || echo "ZERO opencode lines in log"
kill -TERM <pid>
```

Commit message:

```
[agent-base-v2/C] chore: remove opencode source tree (archive-mirrored in legacy/)

The Phase A archive holds sha-identical copies at:
- legacy/daemon/opencode/ (8 modules)
- legacy/daemon/tools/external_opencode.py
- legacy/agents/_prompt_system/innate-skills/opencode/skill.md + skill.md.bak

No daemon wire imports these files anymore (commit C.1 stripped them).
[add sha-equality proof lines from Step 0]
```

### Step 3 — Commit C.3: TEST FILES MOVE + MOCK STRIPS (test suite must run green AFTER this commit)

Operations:
- `git mv tests/opencode/ legacy/tests/opencode/` (move 12 files preserving directory)
- `git mv docs/bugs/external-opencode-wait-latency.md legacy/docs/bugs/`
- `git mv docs/bugs/opencode-session-manager-memory-leak.md legacy/docs/bugs/`
- `git mv test/packs/opencode_native_tools_unit_test.sh legacy/test/packs/`
- `git mv test/packs/ab/top_opencode_unit_test.sh legacy/test/packs/ab/`
- `git mv test/packs/regression_opencode_e2e_test.sh legacy/test/packs/`
- Apply the Tier-4 mock strips listed in §4.6 (15 files × N line removals).
- `tests/test_spawn_instance_instructive_errors.py:26,42,50,362,368` — verify the surrounding test bodies are `@pytest.mark.skip(reason="Instructive error messages not yet implemented")`; if all 4 cite locations are inside skipped test methods, no edit required (the opencode-factory path doesn't run; the module-load path stays valid because `daemon.opencode` is no longer imported after C.1, and the test mocks `"opencode"` as a generic skill-name string); if any cite location is in a NON-skipped method, REPLACE `"opencode"` with another skill name that has an actual prompt file in `agents/_prompt_system/innate-skills/`.

Verification before committing:

```
# No test file outside legacy/ references opencode
grep -rn 'opencode\|OpenCode' tests/ --include='*.py' --include='*.sh' | head -30
# Should return ZERO matches (all matches should be in legacy/tests/, not in tests/)

# Smoke-test that no test fails to IMPORT (collection-only pytest)
python -m pytest --collect-only -q tests/ 2>&1 | grep -i 'import\|error\|fail' | head -20
# Must return ZERO ImportError, AttributeError, or "module not found" rows

# Run a small representative slice
python -m pytest tests/test_council_tools.py tests/test_manager_shutdown.py tests/test_governor_integration.py -x 2>&1 | tail -30
# Must complete without error
```

Full-suite verification (this is the moment-of-truth for risk #7):

```
cd /home/nea/ensemble-src-wt-agent-base-v2
python -m pytest tests/ -x --timeout=120 2>&1 | tee /tmp/c3_pytest.log
# Look for the line "X passed" with X > 0 and zero FAIL/ERROR
# Expected: a number comparable to the pre-Phase-C test count MINUS the moved-out opencode-specific count
# Record the final passed/failed/skipped counts in the commit message
```

Commit message:

```
[agent-base-v2/C] test: archive opencode-specific tests + strip opencode mocks from shared tests

Move:
- tests/opencode/ → legacy/tests/opencode/ (12 files)
- docs/bugs/external-opencode-wait-latency.md, opencode-session-manager-memory-leak.md → legacy/docs/bugs/
- test/packs/opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh → legacy/test/packs/

Strip opencode mocks (15 files):
- 14 × `patch("daemon.tools.instance.create_opencode_tools", return_value=[])` lines dropped
- 2 × `manager._shutdown_opencode_registry = AsyncMock()` lines dropped
- 1 × `tests/unit/test_coder_agent.py` expected-tool-set entry dropped

Pytest result: [paste final line: "X passed, Y skipped, Z xfailed, 0 failed"]
```

### Step 4 — Commit C.4: TIER-2 + MANIFEST CLEANUPS (no boot change)

Operations:
- `Makefile` — drop `opencode_sessions.db`, `opencode_sessions.db-wal`, `opencode_sessions.db-shm` from the `FILES=` list at the cited lines.
- Tier-2 comment-only cleanups across `daemon/config.py`, `daemon/services/context_injection.py`, `daemon/services/context_messages.py`, `daemon/services/critical_note_gate.py`, `daemon/services/waiting_children_watchdog.py`, `daemon/loader.py`.
- LEAVE `daemon/repositories/task/repository.py:1777` untouched (D5 ambiguity).

Verification before committing:

```
# Search daemon/ for surviving opencode STRING mentions (Tier-2 leftovers, not Tier-1 imports)
grep -rn '\bopencode\b\|\bOpenCode\b' --include='*.py' daemon/ 2>&1 | head -20
# Should return ONLY:
# - daemon/services/keyword_extraction.py (D5: the module STAYS)
# - daemon/repositories/task/repository.py (D5: comment-only, left untouched)
# - (any Tier-2 site the executor decided to leave for a follow-up, with a justification comment)

# Makefile FILES = no opencode_sessions entries
grep -n 'opencode' Makefile
# Must return ZERO matches
```

Boot proof (fenced dev lane; one more time):

```
ENSEMBLE_SELF_ENV=dev PORT=8079 ./dev.sh boot
curl -s http://localhost:8079/livez | jq .
curl -s http://localhost:8079/readyz | jq .
grep -i opencode data/logs/*.log 2>&1 || echo "ZERO opencode lines in log"
kill -TERM <pid>
```

Commit message:

```
[agent-base-v2/C] chore: drop opencode from Makefile backup FILES + Tier-2 comment cleanup

Manifest:
- Makefile: drop opencode_sessions.db, opencode_sessions.db-wal, opencode_sessions.db-shm
  from the backup FILES list. Runtime DBs are boot-recreated state (D7) and should
  not be backed up by the install-backup flow.

Comment-only cleanups (Tier-2):
- daemon/config.py:168: rephrase model_keywords description
- daemon/services/context_injection.py:541,846: drop opencode analogies
- daemon/services/context_messages.py:35,39: drop ADR-13 reference
- daemon/services/critical_note_gate.py:8: drop external_opencode.py from docstring
- daemon/services/waiting_children_watchdog.py:9,752: drop OpenCode subprocess analogy
- daemon/loader.py:226: replace "opencode" example with "question"

Untouched (D5):
- daemon/services/keyword_extraction.py (module stays per D5)
- daemon/repositories/task/repository.py:1777 (D5; comment-only, may not churn)
```

### Step 5 — Commit C.5: BOOT-PROOF EVIDENCE ARTIFACT (no code changes; evidence only)

Operations:
- Write `.agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md` with:
  - Worktree identity: `git rev-parse HEAD` × 2 (before C.1 base + after C.4 head)
  - The 4 commit SHAs (C.1, C.2, C.3, C.4)
  - The full Phase A sha-equality proof (from Step 0)
  - The full Phase B diff-emptiness proof (from Step 0)
  - The fenced dev-booted `/livez` response body
  - The fenced dev-booted `/readyz` response body
  - The full grep -c "opencode" data/logs/*.log result (= 0 expected)
  - The full shutdown sequence (no traceback)
  - The full `python -m pytest tests/` final line
  - The final grep sweep across `daemon/`, `agents/`, `tests/`, `Makefile` for surviving `opencode` mentions, classified into "delete / leftover-comment-tolerated / false-positive"
- Do NOT commit this file with code (it lives in `.agents/shared/planning/agent-base-v2/evidence/` which is project state, not repo state). Instead: copy the artifact path to the commit message of C.4 (or to a final local-only commit on the worktree that does not push to a feature/staging branch). Confirm with dispatcher: if `.agents/shared/planning/...` IS tracked by git, commit normally; if gitignored, leave as local evidence only.

Commit message (if committed):

```
[agent-base-v2/C] evidence: boot proof + final grep sweep artifact

See .agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md
[summary: 4 commits land in 4 chronological steps; all boot proofs pass in fenced
dev lane; test suite green; grep sweep clean modulo D5 keyword_extraction +
D6 docs/mcp-kb-usage.md false-positive + Tier-5 historical records]
```

## 6. Coordination / Decision Gates

### 6.1 `example/SKILL.md`

The task statement says: "optional example/SKILL.md (verify existence; the Phase A worker decides archival — coordinate by referencing phaseA-legacy-archive.md)."

This file targets the EXTERNAL third-party opencode Orchestrator product (`~/bin/opencode_skill`, references sst/opencode-style content), not our service. Phase A likely archived it as a "false positive skip — third-party example." Phase C executor MUST:

1. Read `phaseA-legacy-archive.md` to see whether Phase A addressed it.
2. If Phase A archived it: no C action (file is already in legacy/, working-tree copy may or may not remain).
3. If Phase A left it: EXECUTOR must decide between (a) `git rm example/SKILL.md` (it documents a non-ensemble tool) or (b) `git mv example/SKILL.md legacy/example/SKILL.md` (preserve as "referenced by historical docs"). RECOMMENDED: read `agents/_prompt_system/knowledge.md`, `docs/agents.md`, `docs/skill-evolution.md` for any "see example/SKILL.md" pointer; if pointers exist, archive (b); if no pointers, delete (a). Default: archive (b) — safest.

### 6.2 Phase A archive integrity prerequisite

Phase C cannot safely remove any source-tree file until Phase A's archive holds a sha-identical copy. Executor MUST execute the Step 0 sha-equality check. If any mismatch: STOP and report to dispatcher — do NOT proceed with mismatched archives (silent damage to the shared legacy/ tree violates Cardinal #2).

### 6.3 Base agent innate_skills ordering (R7 — corrected pair set)

Phase C's removal of `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` makes the entry resolve to "no tool categories". If Phase B has NOT yet promoted base agents' meta.json away from `innate_skills: ["opencode", ...]`, the loader at `daemon/loader.py:226` will try to resolve `opencode` as an innate skill and find nothing. This is the opencode-skill loader's graceful fallback path. **Confirmed graceful** by architect §3.6 — `expand_allow_for_innate_skills` uses `.get(skill, [])` (silent no-op) at `daemon/tools/instance.py:171+`; missing skill dir is a `logger.warning` at `daemon/loader.py:389`. After Phase B, no agent declares `["opencode", ...]` in `innate_skills`, so this path is irrelevant post-B. The pre-B boot test still covers it: dev lane boots even with a hypothetical base agent that still declares `["opencode", ...]`. If it does NOT boot clean, Phase C cannot land C.1 before Phase B has run.

Mitigation if ordering inversion discovered at runtime: re-order Phase B to land FIRST (re-run B → verify base.copy diff-empty → then resume C.1). Do not bypass.

**Pair set (R7):** the 5 retiring base agents are exactly `developer / reviewer / tidier / approver / planner`. `agents/leader[v2]` does NOT exist; leader is NOT in the migration set. **The diff loop in Step 0 / Step 1.5 enumerates these five pairs** — no leader. Every `leader` reference in the migration-set context in this plan is a STALE hit; `attestation leader refs in daemon/services/instance_lifecycle.py:2305,4753` (verify-only, name pin) is unrelated and stays untouched.

### 6.4 Frozen-binary validation list consistency (`KNOWN_TOOL_NAMES`) (R4 — corrected)

`_tool_registry.py` reads `KNOWN_TOOL_NAMES` to validate every dynamically-imported tool. Removing an import line without removing the corresponding `KNOWN_TOOL_NAMES` string causes a "known tool not imported" warning that may fatal-error on strict mode. **ALL FOUR sites** (NOT three; R4 correction) must land in the same commit:

1. `daemon/tools/instance.py:155-167` — `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` grant-map row
2. `daemon/tools/instance.py:259` — `from .external_opencode import create_opencode_tools` import line
3. `daemon/tools/instance.py:5815-5819` — `opencode_tool_list = create_opencode_tools(manager, current_instance_id)` + `tools.extend(opencode_tool_list)` factory-call block (P8 correction: `tools.extend` is at `:5819`, one past the prior range end)
4. `daemon/tools/_tool_registry.py:603` + `:764-771` — module-map row + the 8 `external_opencode_*` KNOWN_TOOL_NAMES rows

This 4-site removal discipline is even documented in-repo at `daemon/tools/upgrade_tools.py:141`. Phase C's C.1 covers all 4 sites in one commit — do not split. Architect §3.1 confirmed KNOWN_TOOL_NAMES excision is safe in all three planes (source / frozen-binary / ledger).

### 6.5 Boot-fence enforcement

All boot proofs (Steps 1, 2, 4, and the final Step 5 evidence) execute in the WORKTREE'S fenced dev lane only. The fence is `ENSEMBLE_SELF_ENV=dev` + worktree-local `data/` + port 8079 + ensemble_dev DB. NEVER boot ambient-env (env-poison incident history ×6). NEVER contact live (`/home/nea/ensemble-src` is DIRTY).

## 7. Phase Rollback

**P6 (architect §5 / §7): Phase C rolls back as a UNIT.** C.1 and C.2 are mutually broken in partial-revert states:

- Reverting C.2 alone leaves `daemon/manager.py:78` with `from .opencode import OpenCodeSessionRegistry, create_opencode_session_repository` — but the `daemon/opencode/` package is gone → `ModuleNotFoundError` on next boot.
- Reverting C.1 alone leaves the import wiring removed but the modules + tool factories still on disk → import-dead-code (no caller invokes them, but they are still importable; silent resource leak; non-fatal but regressive).
- Reverting C.3 alone leaves `tests/opencode/` and the test packs live in `tests/` while `daemon/opencode/` is gone from `daemon/` → collection `ModuleNotFoundError` on every `test_*.py` in `tests/opencode/`.
- Reverting C.4 alone leaves the in-place TOUCH edits live in `tests/` while C.1-C.3 are reverted → mix of removed-mock-callers + restored-mock-calls (cosmetic; the test mocks at `tests/test_manager_shutdown.py:126` etc. point at `_shutdown_opencode_registry` which exists after C.2 reverts-in-part; mostly self-healing but ugly).

**The only safe Phase C rollback is to revert C.4 + C.3 + C.2 + C.1 as a single unit.** Any partial revert is unsafe.

Rollback sequence (whole-C unit):

```bash
cd /home/nea/ensemble-src-wt-agent-base-v2
# Reverse order: revert C.5 (evidence artifact) first, then C.4, C.3, C.2, C.1
git revert --no-commit C.5 2>/dev/null || true   # C.5 may be a no-op if evidence not committed
git revert --no-commit C.4
git revert --no-commit C.3
git revert --no-commit C.2
git revert --no-commit C.1
git commit -m "revert: Phase C opencode excision (whole-C unit, P6 binding)"
# Or in a single linear sequence:
git log --oneline C.1^..C.4 | tac | xargs -I{} git revert --no-commit {}
git commit -m "revert: Phase C opencode excision (whole-C unit, P6 binding)"
```

For non-linear follow-ups: `git reset --hard <last-good-sha>` where `<last-good-sha>` is the tip of C.4 (or C.5 if you kept it) OR the base tip `885a19d4f` if rolling back all of Phase C.

The archived copies in `legacy/` are NEVER touched by rollback (they live under a separate top-level dir that Phase B/C commits do not edit; only Phase A can add to or modify `legacy/`). Re-running Phase C after a rollback is safe — the source-tree files are restored by the revert, the legacy/ archives are still in place.

D8 hardening: the rollback is local to `feature/agent-base-v2`. No live promote happened; no remote was pushed; no main checkout touched.

## 8. Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Tier-1 wiring-removal ordering vs. boot** — a commit strips an opencode import without removing the source file, then a subsequent commit deletes the file while a transient import test still references it; OR a commit strips `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` before base agents' meta.json files have stopped declaring `innate_skills: ["opencode"]`, leaving a runtime "no tool categories for this innate skill" error on every base-agent cold start. | High (boot broken) | Medium | (a) ALL Tier-1 wiring edits and ALL source-file removals land in C.1 + C.2 — no intermediate commit has a partial state. (b) Boot proof runs at the END of each commit. (c) Phase B's `base := v2` copy is a HARD PREREQUISITE before C.1 lands (verified by `diff -r` empty). (d) `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` is dropped in C.1; if base agent meta.json still references `["opencode", ...]`, the loader's graceful fallback is the only line of defense — boot MUST still pass the livez/readyz checks. (e) Risk #6 mitigations further reduce the surface. |
| 2 | **KNOWN_TOOL_NAMES frozen-binary drift** — leaving any of the 8 `external_opencode_*` rows in `_tool_registry.py:764-771` after the factory + module-map removal surfaces "unknown tool" validation errors when any test suite or external binary exercises the registry. | High | Low | (a) All 3 removal sites (factory call, module map, KNOWN_TOOL_NAMES) land in C.1 together. (b) Static check before C.1 commit: `grep -n external_opencode daemon/tools/_tool_registry.py` returns 0 lines. (c) grep sweep at C.5 evidence-time confirms cleanliness. |
| 3 | **Test-file move vs. leftover imports** — moving `tests/opencode/` and the two opencode-specific test files to legacy/ before stripping the opencode mocks from shared tests surfaces ImportErrors at test-collection time. | Medium | Low | (a) `git mv` and mock-strip edits land in C.3 together. (b) `python -m pytest --collect-only tests/` runs BEFORE commit C.3 lands to surface any unstripped mocks. (c) The mock-strip list (§4.6) covers all 15 known files; the executor MUST re-grep at execution time for new mocks not in this plan. |
| 4 | **Phase A archive integrity** — Phase A's archive holds a copy that does NOT byte-match the working-tree file (drift between archive and live). C git-rm operates on a file whose archive copy differs from the truth. | High | Low | (a) Step 0 sha-equality check is BLOCKING. (b) If sha mismatches: STOP, report to dispatcher, do not git-rm. (c) Cardinal #2 of "do not silently damage shared state" is the binding constraint. |
| 5 | **Phase B base copy not yet proven** — base agent dirs (`agents/developer/`, `agents/reviewer/`, `agents/tidier/`, `agents/approver/`, `agents/planner/` — R7: leader is NOT in the set) still hold pre-v2 (opencode-era) meta.json with `innate_skills: ["opencode", ...]`. C strips the daemon-side `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` entry → base agents' opencode skill loads with no prompt and no tool categories. | Medium | Medium | (a) Step 0 / Step 1.5 `diff -r` over the FIVE pairs (developer/reviewer/tidier/approver/planner) is BLOCKING. (b) If any diff non-empty: STOP. Phase B must land first. (c) Boot proof in C.1 must exercise a base agent and confirm graceful fallback works. The architect §3.6 confirmed fallback is safe via `.get(skill, [])` at `daemon/tools/instance.py:171+` and the missing-skill warning at `daemon/loader.py:389`; if these do NOT work in practice, Phase C cannot land C.1 ahead of Phase B — re-plan. |
| 6 | **Name-pin churn** — executors accidentally edit `TOOL_REQUIRED_AGENTS`, `REPORT_REPAIR_EXCLUDED_AGENTS`, `default_agent_versions`, `spawn-by-name` sites, default `"ari"` pins, attestation leader refs, `frontend/.../jobs.component.ts:2389` thinking they're "opencode references" when they're not. | Low | Medium | (a) The 4.2 OOS section lists all known-safe name pins. (b) Executor MUST re-grep at execution time before editing any of those files. (c) The grep sweep at C.5 evidence-time will detect any churn. |
| 7 | **Full test pack green** — after stripping opencode mocks from shared tests and moving opencode-specific tests, the remaining suite must still pass. A missed mock-strip or a regression in the strips surfaces as a CI failure. | High | Medium | (a) C.3 commit message MUST include the final `pytest` line. (b) Executor MUST re-grep for mocks at execution time (this plan's §4.6 list is exhaustive for known cases; new mocks may have been added since this plan was written). (c) If a test fails post-C.3: investigate the failure site; if the failure is opencode-related (importing daemon.opencode, patching daemon.tools.instance.create_opencode_tools, or instantiating `_opencode_registry`), the test needs another strip; if unrelated, it is a pre-existing failure and Phase C is not responsible. |
| 8 | **Tier-2 comment-cleanup scope creep** — cleaning every opencode reference in code (Tier-2) risks touching code adjacent to the change. | Low | Medium | (a) Tier-2 is OPTIONAL. If the executor is uncertain, leave the comment and document it as a follow-up. (b) D5 explicitly says `daemon/services/keyword_extraction.py` and `daemon/repositories/task/repository.py:1777` may not churn. (c) Tier-2 edits that touch code paths (e.g., `daemon.services.keyword_extraction.extract_keywords`) carry higher risk than pure-docstring edits. |
| 9 | **Tier-5 historical record churn** — executors accidentally "clean up" `.agents/tester/RESULTS/*` or `.agents/*/LESSONS/*` files that reference opencode-era filenames. | Low | Low | (a) OOS section lists Tier-5 as a binding no-touch list. (b) grep sweep at C.5 ignores `.agents/**/RESULTS/**` and `.agents/**/LESSONS/**` — those mentions are expected to remain. |
| 10 | **Coverage of grep-final-sweep false positives** — the post-C.5 grep sweep may surface opencode mentions that look actionable but are FALSE POSITIVES (`docs/mcp-kb-usage.md`, `plugins/opendesign/copy_freely/design-systems/opencode-ai/`, Tier-5 historical records, `daemon/services/keyword_extraction.py` D5 module, legacy/ archive itself). Executor must classify carefully, not blanket-delete. | Low | High | (a) The plan's known-clean-sweep exclusions list (D5, D6, D7, Tier-5, legacy/) is enumerated in §2 OOS. (b) The evidence artifact documents the classification. (c) The grep sweep with appropriate exclusions (`grep --exclude-dir=legacy --exclude-dir=.agents --exclude-dir=plugins --exclude=docs/mcp-kb-usage.md`) is the recommended command shape. |
| 11 | **Worktree `dev.sh` invocation drift** — the exact boot command (`./dev.sh boot`, `python run_app.py`, or another) may have been renamed or restructured since this plan. Executor must verify. | Medium | Low | (a) Each Step's boot proof says "verify exact invocation at use time." (b) Cross-reference the worktree's CLAUDE.md, README.md, or scripts directory at execution time. (c) If the boot command is unknown, ASK DISPATCHER — do not improvise. |
| 12 | **Worktree-base branch races** — if Phase A or Phase B is still running on a parallel worker while C executes, file states may race. | Medium | Medium | (a) Step 0 sha-equality and diff-empty checks anchor the baseline. (b) C is atomic at the commit level; if a parallel-worker commit lands DURING C's execution, C aborts and restarts from a re-anchored SHA. |

## 9. Definition of Done

> **C-1 note (added by fix pass):** This section pre-dates the §13 addendum-era design. Where §9 conflicts with §13 (specifically the test-file move-vs-edit decision in DoD #13, which was corrected from "no longer in working tree" to "edited in place per §13.5, zero opencode references post-C.4"), **§13 is authoritative**. The C-1 fix-pass correction here updates DoD #13 to match the authoritative design.

Phase C is COMPLETE when ALL of the following are true (each is independently checkable, and each has a concrete measurement method):

| # | Criterion | How to measure | Threshold / pass condition |
|---|-----------|----------------|-----------|
| 1 | Daemon source tree is free of opencode implementation files | `find daemon/ agents/_prompt_system/ -name '*opencode*' -o -name '*OpenCode*'` | 0 results |
| 2 | `daemon/opencode/` package no longer exists in working tree | `ls daemon/opencode/` | "No such file or directory" |
| 3 | `daemon/tools/external_opencode.py` no longer exists in working tree | `ls daemon/tools/external_opencode.py` | "No such file or directory" |
| 4 | `agents/_prompt_system/innate-skills/opencode/` no longer exists in working tree | `ls agents/_prompt_system/innate-skills/opencode/` | "No such file or directory" |
| 5 | `legacy/` archive holds byte-identical copies of all 11 removed files | `sha256sum legacy/... daemon-opencode-original-shas` (compare pre-C snapshot to post-C archive) | All 11 sha equality |
| 6 | Daemon code has zero opencode imports | `grep -rn 'from .opencode import\|from daemon.opencode\|from .external_opencode import\|from daemon.tools.external_opencode' daemon/` | 0 results |
| 7 | Daemon code has zero opencode factory calls | `grep -rn 'create_opencode_tools\|create_opencode_session_repository\|OpenCodeSessionRegistry' daemon/` | 0 results |
| 8 | `_tool_registry.py` has zero opencode references | `grep -n 'external_opencode' daemon/tools/_tool_registry.py` | 0 results |
| 9 | `manager.py` has zero opencode references (excluding D5 exception list) | `grep -n 'opencode\|OpenCode' daemon/manager.py` | 0 results |
| 10 | `Makefile` FILES list has zero opencode_sessions entries | `grep -n 'opencode' Makefile` | 0 results |
| 11 | Tests outside `legacy/` have zero opencode symbols | `grep -rn 'create_opencode_tools\|_opencode_registry\|opencode_sessions\|_shutdown_opencode_registry' tests/ --include='*.py' --include='*.sh'` | 0 results outside legacy/ |
| 12 | `tests/opencode/` no longer in working tree | `ls tests/opencode/` | "No such file or directory" |
| 13 | `tests/test_innate_skills_refactoring.py` and `tests/migration/test_jsonb_migration.py` edited in place per §13.5; BOTH files remain in the working tree post-C.4 with zero opencode references | `ls tests/test_innate_skills_refactoring.py tests/migration/test_jsonb_migration.py` shows both present; `grep -n opencode tests/test_innate_skills_refactoring.py tests/migration/test_jsonb_migration.py` returns 0 lines | Both files present + grep-zero opencode matches |
| 14 | Boot proof PASS in fenced dev lane | Curl `/livez` + `/readyz` after `ENSEMBLE_SELF_ENV=dev PORT=8079 ./dev.sh boot` (verify invocation) | Both 200 OK |
| 15 | Boot log has zero opencode mentions | `grep -ci 'opencode' data/logs/*.log` | 0 (or matches the known-clean-sweep exclusions list — see below) |
| 16 | Clean shutdown traceback-free | `kill -TERM <pid>` then tail log | 0 tracebacks, 0 errors |
| 17 | Full test suite passes | `python -m pytest tests/ --timeout=120` | Final line: "X passed, 0 failed" (any number of skips/xfails is acceptable) |
| 18 | 4 commits land on `feature/agent-base-v2` worktree | `git log --oneline C.0..C.4` (where C.0 = base @ 885a19d4f) | 4 commits with messages matching the patterns in Steps 1-4 |
| 19 | Boot-proof evidence artifact exists and is complete | `cat .agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md` | Contains: 4 commit SHAs, Phase A sha proof, Phase B diff proof, livez + readyz response bodies, log grep result, pytest final line, final grep sweep classification table |
| 20 | No live-system contact | `git log --all --grep="ENSEMBLE_UPGRADE_LIVE\|promote"` shows no promotion commits | 0 promotion commits during C's execution window |
| 21 | No main-checkout mutation | `git -C /home/nea/ensemble-src status` returns dirty-state UNCHANGED from pre-C baseline | Same dirty-state SHA, no new files |
| 22 | Tier-5 historical records untouched | `.agents/tester/RESULTS/`, `.agents/*/RESULTS/`, `.agents/*/LESSONS/` files have mtimes within Phase C's execution window OR are unchanged | 0 mtime changes in RESULTS/LESSONS |
| 23 | D7 runtime DBs untouched | `find . -name 'data*/opencode_sessions.db'` returns the same files pre/post C | Same files (and the file becomes orphaned-but-harmless; not deleted by C) |

## 10. Known Clean-Sweep Exclusions (final grep sweep classifiers)

The post-C.5 grep sweep `grep -rn '\bopencode\b\|\bOpenCode\b' --include='*.py' --include='*.sh' --include='*.md' --include='*.json' --include='*.yaml' daemon/ agents/ tests/ Makefile docs/ plugins/` is EXPECTED to return matches in the following locations. The executor MUST classify each match into one of:

- **EXPECTED_LEAVE (D5)** — `daemon/services/keyword_extraction.py` (module per D5 stays).
- **EXPECTED_LEAVE (D6)** — `docs/mcp-kb-usage.md` (upstream sst/opencode product, NOT our service).
- **EXPECTED_LEAVE (D7)** — runtime DBs `data*/opencode_sessions.db` (boot-recreated state, untouched).
- **EXPECTED_LEAVE (Tier-5)** — `.agents/*/RESULTS/**` and `.agents/*/LESSONS/**` historical records.
- **EXPECTED_LEAVE (legacy/)** — `legacy/daemon/opencode/**`, `legacy/daemon/tools/external_opencode.py`, `legacy/agents/_prompt_system/innate-skills/opencode/**`, `legacy/tests/opencode/**`, `legacy/docs/bugs/{external-opencode-wait-latency,opencode-session-manager-memory-leak}.md`, `legacy/test/packs/**opencode*`, `legacy/.agents/shared/planning/opencode-native-tools/**`, `legacy/example/SKILL.md`. **C-1 fix:** `tests/test_innate_skills_refactoring.py` and `tests/migration/test_jsonb_migration.py` are NOT in this row — they stay IN-TREE (edited in place in C.4) and the post-C.4 grep sweep targets ZERO opencode matches within them.
- **EXPECTED_LEAVE (Phase D scope)** — `docs/agents.md`, `docs/skill-evolution.md`, `docs/test-suite-known-issues.md`, `docs/agent-prompt-writing-guide.md §9`, `README.md`, `AGENTS.md`, `CHANGELOG.md`. Phase D owns these.
- **EXPECTED_LEAVE (history)** — `LESSONS/architecture-migration-status-2026-06-26.md`, `.slim/cartography.json`, `backup/agents/*`, `agents/devops/soul.md`, `agents/maintenancer/knowledge/*` historical references.
- **EXPECTED_LEAVE (third-party)** — `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` (vendored external design system; not our opencode service).
- **ACTIONABLE (v2 cleanup)** — `docs/plans/*`, `docs/optimization/*`, `docs/mcp-integration.md`, `agents/{developer,reviewer,tidier,approver,planner}/**` body (opencode-mention comments in rule.md, soul.md, tools_note.md — Phase B already converted the meta.json; body mentions may still be in the opencode-era prose and should be cleaned by D, not C). R7 reminder: `agents/leader/**` is NOT in the migration set; leader base + leader are unchanged by Phase C — but leader body mentions may still warrant a Phase D doc rephrase.
- **NEW_ACTIONABLE (must-fix-now)** — any match NOT in the EXPECTED_LEAVE categories above. The grep sweep MUST return zero matches in this category.

The classification table is the LAST section of the boot-proof artifact at `.agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md`.

## 11. Cross-Reference & Sibling Handoff

- **Sibling phases** (consumed by C / consume C's output):
  - `phaseA-legacy-archive.md` — predecessor; C verifies archive integrity in Step 0.
  - `phaseB-base-copy.md` — predecessor; C verifies base-copy diff in Step 0.
  - `phaseD-references-docs.md` — sibling; phaseD owns user-facing docs.
  - `phaseE-verification-evidence.md` — successor; phaseE consumes C's boot-proof artifact.
- **Handoff to Phase E**: `evidence/phaseC-boot-proof.md` carries the 4 commit SHAs, the fenced-dev-lane boot transcript, the test-suite-green log, the final grep-sweep classification table. Phase E re-uses these as its evidence sources.
- **Touch surface summary for D**: Phase D may want to know that Phase C preserved opencode mentions in `daemon/services/keyword_extraction.py` (D5) and `docs/mcp-kb-usage.md` (D6) so D does not double-clean them.

## 12. Process Note

This phase plan is the artifact for the planner (and downstream executor). The on-disk file is the deliverable; downstream phases consume it. Phase C executor MUST re-grep every load-bearing cite at execution time ("grep at use time, sites drift") and re-verify the Step 0 sha + diff preconditions BEFORE any commit.

## 13. Authoritative Verdict Incorporation (binding addendum — supersedes prior framings)

This section is **binding** and supersedes any earlier pre-addendum text in §1–§12 where they conflict. It incorporates the leader-relayed exploration addendum in full: pending-classification placeholders are replaced with the two verdicts, BRANCH 1 is selected by evidence (boundary-rule machinery is retained but expected-unused), the MOVE/TOUCH/LEAVE sets are consolidated below, the Phase-B-coupled ordering gate is made explicit, and "pending addendum" markers are dropped.

### 13.1 VERDICT 1 — One retirement unit, two named roles

`daemon/opencode/` is a **single package**: a line-by-line port of the opencode_skill Go binary (`daemon/opencode/server.py:168+` ports `opencode_skill_src/internal/daemon/server.go:230-507`). The "communication service" the original brief named is NOT a separate sibling — it is **the protocol layer INSIDE the session lane**, specifically:

- `server.py` action dispatch (PROMPT / COMMAND / ANSWER / RESUME / INIT_SESSION / ABORT_SESSION) +
- question-poll/relay logic

Its sole consumer is `daemon/tools/external_opencode.py:20-21` (the tool-layer shim). The single import graph is `external_opencode.py → server.py → registry/session_manager → client`, with a single SQLite store (`opencode_sessions`) and a single init site (`daemon/manager.py:1427-1436`). The `daemon/opencode/__init__.py:13` claim that the dispatch is "used by the ensemble HTTP router" is **STALE** — zero routers mount any opencode endpoint (30 routers + `api.py` verified clean by the addendum).

**No split-removal is needed.** One move, two roles named (comm-lane role = protocol dispatch in server.py; session/API coding-worker role = client/session_manager/registry/state/repository). Phase A's archive holds the WHOLE package under `legacy/daemon/opencode/`; Phase C removes the WHOLE package.

### 13.2 VERDICT 2 — LIVE-DEPENDENCY: BRANCH 1 (FULL-STACK REMOVAL) selected by evidence

The branch point in §1 / §5 Step 0.5 is **resolved by evidence → BRANCH 1 full-stack removal**; no isolated exception. **Do NOT** silently resolve this in the future; this verdict is leader-relayed exploration evidence recorded explicitly. Boundary-rule machinery is retained (untouched) so that the plan stays valid under either branch, but BRANCH 2 is **expected-unused** for this mission.

Evidence:

| # | Surface | Evidence | Verdict |
|---|---------|----------|---------|
| (a) | **coder agent** | `grep opencode agents/coder/` returns zero hits; coder's `meta.json` declares `innate_skills: ["todo", "chart", "dynamic-skill"]` and tools.allow does NOT include the `external_opencode` category (verified `agents/coder/meta.json`). The grep guard tests in `tests/unit/test_coder_agent.py` (`test_innate_skills_does_not_contain_opencode` :144, `test_tools_allow_does_not_contain_opencode` :168, `TestCoderNoOpencodeContamination` :369+ / :510 / :253) are **contamination GUARDS** — they keep PASSING post-Phase-C. The executor MUST edit the `TOOL_CATEGORIES` mirror in `tests/unit/test_coder_agent.py:40-43` to drop the now-stale `external_opencode` entry WITHOUT weakening the guards. | OPENCODE-FREE BY DESIGN; guards stay green |
| (b) | **5 retiring base agents' meta.json** (pre-Phase-B) | `["opencode", "chart", "todo"]` appears ONLY in the 5 retiring base agents' meta.json. ZERO meta.json files (post-base list of 38+ agents) list `external_opencode` in `tools.allow`. | After Phase B base := v2 copy lands, NO agent carries opencode in `innate_skills` or `tools.allow` |
| (c) | **Runtime invocation outside tests** | Manager wiring is PASSIVE: init creates a registry object (`daemon/manager.py:1427-1436`), but startup recovery is DISABLED (`daemon/manager.py:3325-3338` block is commented-out per existing plan §4.4 cite), and idle eviction touches in-memory managers only (no daemon-side HTTP listener). No HTTP request can reach the opencode service until an agent tool call is granted; **no granted caller exists** in the post-Phase-B state. **DEAD.** | Verdict: BRANCH 1 — full removal |
| (d) | **MCP config layer** | `daemon/mcp/` has zero opencode-named entries (verified: built-ins are `context7`, `plane`, `webfetch` only; `daemon/mcp/builtin_servers/__init__.py` registry lists exactly these three). `config.yaml` has zero opencode references. No `.agents/*/skill-set.yaml` preset mounts an opencode MCP. | NO MCP-config removal work exists; the recon I performed (`find daemon/mcp/` + grep over `.yaml/.yml/.json/.toml` + `daemon/config.py`) confirms zero candidates |
| (e) | **Port 4095 (local OpenCode HTTP server)** | Nothing listening; `~/.opencode_skill/` and `~/.opencode/` absent on this host (per the addendum). The upstream binary the daemon's `OpenCodeClient` was designed to talk to is not running. | Functionally orphan; removal is safe |
| (f) | **`daemon/services/keyword_extraction.py` + `daemon/services/context_injection.py`** | These are LIVE-SHARED infra with active non-opencode callers (`attestation_report_judge.py:35-328`, `graph.py:10794`, `plugin_subsystem/opendesign/generate.py:650`). The modules STAY (D5 explicit + addendum verification). The `opencode` mentions in their comment lines / docstrings / log strings are caller-removal commentary — they go away as commented references but the modules themselves are untouched. | STAY (D5 + addendum); comment rephrases only |

The PLANNING-BEFORE-EVIDENCE placeholder is now resolved; the executor does NOT need to query the leader further to determine the branch. If a future mission revives the opencode service (DEAD → LIVE), the verdict must be re-derived; this verdict is for THIS mission only.

### 13.3 Branch-point restructuring (binding)

The branch structure from the prior §1 + §5 Step 0.5 framing is preserved in skeleton form but is **expected-unused**:

- **BRANCH 1 (SELECTED):** full-stack removal — sever everything listed in §13.4 / §13.5; archive already lives in `legacy/` per Phase A; ensemble's own system unregressed.
- **BRANCH 2 (RETAINED, NOT EXERCISED):** isolated exception if a verdict (or a future surface scan) marks a live dependency on a single module. Under BRANCH 2, the executor (a) keeps that single module in place, (b) severs everything around it (its references from manager.py / instance.py / _tool_registry.py / any other opencode imports), (c) flags the exception as a USER DECISION ITEM with two options (re-point to native system vs retire the agent). The boot-checkpoint ordering in §5 and the commit sequence must remain valid under BRANCH 2 — the executor holding BRANCH 2 just edits the commit-by-commit file list to retain the live module instead of moving it.

The executor's Step 0 / Step 0.5 reads of §13.2 evidence confirm BRANCH 1 in writing before any commit lands. If the executor reads the verdict and judges a contradiction between verdict evidence and observed runtime, the executor STOPS and reports to the dispatcher — the boundary rule says never silently resolve.

### 13.4 Consolidated MOVE SET (final — files git-rm / git-mv to `legacy/` mirror)

After Phase A's archive is verified, this phase removes from the source tree:

| Category | Path(s) | Count |
|---|---|---|
| Daemon opencode implementation package | `daemon/opencode/{__init__, client, constants, registry, repository, server, session_manager, state}.py` | 8 |
| Daemon opencode tool shim | `daemon/tools/external_opencode.py` | 1 |
| Innate-skill prompt | `agents/_prompt_system/innate-skills/opencode/skill.md` + `skill.md.bak` | 2 |
| Opencode unit tests | `tests/opencode/` (12 files: `__init__.py`, `conftest.py`, `test_client.py`, `test_error_propagation_edge_cases.py`, `test_integration.py`, `test_registry.py`, `test_repository.py`, `test_server.py`, `test_session_manager.py`, `test_state.py`, `test_table_creation.py`, `test_tools.py`) | 12 |
| Test-pack shell scripts | `test/packs/opencode_native_tools_unit_test.sh`, `test/packs/ab/top_opencode_unit_test.sh`, `test/packs/regression_opencode_e2e_test.sh` | 3 |
| Historical planning docs | `.agents/shared/planning/opencode-native-tools/{go-to-python-parity-check.md, plan-overview.md, phase1-plan.md, phase2-plan.md, phase3-plan.md, phase4-plan.md, phase5-plan.md}` | 7 |
| Opencode bug docs | `docs/bugs/external-opencode-wait-latency.md`, `docs/bugs/opencode-session-manager-memory-leak.md` | 2 |
| Example Go-binary contract spec | `example/SKILL.md` (third-party `disillusioners/opencode-skills` contract; archive only) | 1 |

Total: 36 files / directories removed from active source tree, all with sha-identical copies in `legacy/`.

### 13.5 Consolidated TOUCH SET (final — in-place edits; may span multiple commits)

| File | Cite (grep-verify at use time) | Edit |
|---|---|---|
| `daemon/manager.py` | `:78`, `:1420-1436`, `:2895-2910` (opencode_registry property; the existing-cite `:2902-2910` is within drift tolerance), `:3325-3338`, `:5948-5956`, `:6750-6751` (jsonb targets), `:13033`, `:13078-13103` | Strip opencode imports + init + accessor + recovery block + eviction + DO-block schema rows + shutdown tuple entry + `_shutdown_opencode_registry` method (call site + method body together) |
| `daemon/tools/instance.py` | `:155-167` (INNATE_SKILL_TOOL_CATEGORIES grant-map row), `:259` (`from .external_opencode import create_opencode_tools`), `:5815-5819` (P8 — `tools.extend(opencode_tool_list)` is at `:5819`, one past the prior range end) | Drop `opencode` row from grant-map + drop the import + drop the factory-call block (`opencode_tool_list = create_opencode_tools(...)` + `tools.extend(...)`) + drop the surrounding comment header (`:5816`) |
| `daemon/tools/_tool_registry.py` | `:603` (module-map row), `:764-771` (KNOWN_TOOL_NAMES) | Drop the `"external_opencode"` module-map row + drop the 8 `external_opencode_*` KNOWN_TOOL_NAMES rows (all four sites — instance.py grant-map + instance.py import + instance.py factory-call + _tool_registry.py module-map + _tool_registry.py KNOWN_TOOL_NAMES — land in the same commit per risk #2 / §6.4) |
| `Makefile` | **`:173-174` comment + `:186` FILES** (R5: dropping only FILES entries leaves the comment → own gate `grep -n 'opencode' Makefile → 0` FAILS) | Rephrase the comment to drop the "opencode_sessions.db (always a local SQLite file, never migrated to postgres)" wording; drop `opencode_sessions.db` + WAL/SHM siblings from the `FILES=` backup list |
| `tests/unit/test_coder_agent.py` | `:40-43` (TOOL_CATEGORIES mirror) | Drop the `"external_opencode"` entry from the expected tool-set fixture. **DO NOT** weaken the opencode-contamination guard tests at `:144`, `:168`, `:369+`, `:510`, `:253` — those tests KEEP PASSING. |
| `tests/unit/test_wanderer_agent.py` | `:41-44` (P2 — same stale `external_opencode` mirror) | Drop the `"external_opencode"` entry from the expected tool-set fixture. Mirror is passed explicitly, never compared to the live map; self-contained, green either way. |
| `tests/test_innate_skills_refactoring.py` | (whole file, in-place; P1 — enumerate **27 cite locations across R1 + R2 + P1 (3 R1 + 8 R2 + 16 P1)**: P1 sites `:11`, `:14`, `:56`, `:59-65`, `:84-85`, `:94`, `:98`, `:227`, `:232-236`, `:241`, `:271`, `:275`, `:280`, `:287-289`, `:297-301`, `:353`; R1 sites `:710-716`, `:750-764`, `:827-860`; R2 sites `:68-69`, `:75`, `:106-107`, `:194-197`, `:223-226`, `:366-367`, `:497`, `:518`) | Replace "opencode" examples with another innate skill (e.g. `"chart"` / `"todo"` / `"dynamic-skill"` / `"test-skill"`) so the suite no longer references the opencode service. Keep the in-place test asserting the INNATE-SKILL LOADER works for any skill name; only the fixture strings change. **Accepted RED WINDOW: this file goes RED at Phase-B landing** (the tuples at `:59-65` declare `("developer", ["opencode","chart","todo"], "OpenCode-Skill")` etc. — these are opencode-era fixtures that mismatch post-B base metas; the file is expected to fail until C.4 lands the in-place edits). |
| `tests/migration/test_jsonb_migration.py` | (R2 — full-file opencode surface, not just `:70-107`): **`:68-69` (comment)**, **`:75` (module-level `from daemon.opencode.repository import OpenCodeSessionRecord` — breaks pytest COLLECTION for the entire file post-C.2)**, **`:106-107` (pair rows in `JSONB_MIGRATION_PAIRS`)**, **`:194-197` AND `:223-226` (fixture bodies calling `OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)`)**, **`:366-367` (embedded `_DO_BLOCK_SQL` rows that mirror `daemon/manager.py:6750-6751` — must edit in sync per the test's own header comment)**, **`:497` (second method-scoped import)**, **`:518` (`"opencode_sessions": OpenCodeSessionRecord` in `table_to_class`)** | (a) Drop the `:68-69` opencode reference from the comment block. (b) Drop the `:75` module-level import — this single line is the highest-impact removal since it breaks pytest COLLECTION for the whole file post-C.2. (c) Drop the `:106-107` `("opencode_sessions", "latest_response"/"questions")` pair rows from `JSONB_MIGRATION_PAIRS`; update `assert len(JSONB_MIGRATION_PAIRS) == 17` → `== 15`. (d) Drop or rewrite the `:194-197` / `:223-226` fixture bodies that call `OpenCodeSessionRecord.__table__.create(...)`. (e) Edit `:366-367` to drop the 2 `opencode_sessions` rows from `_DO_BLOCK_SQL` (mirrors `manager.py:6750-6751` removal). (f) Drop the `:497` method-scoped import. (g) Drop the `:518` `"opencode_sessions": OpenCodeSessionRecord` row from `table_to_class`. (h) Update the inline comment at the top of the file to reflect 15 pairs. |
| `tests/test_tool_filter.py` | (R1 — 3 tests go RED at C.1 map-row strip): **`:710-716` `test_opencode_innate_skill_adds_external_opencode_category`**, **`:750-764` `test_multiple_innate_skills`**, **`:827-860` `test_apply_tool_filter_grants_opencode_tools_for_innate_skill`**. (Deny test `:862-887` and no-op tests `:707, :717-722` pass coincidentally — no edit.) | Rephrase to a surviving skill (e.g. `"chart"` / `"test-skill"`); preserve the suite's intent (asserts the loader's expand-helper works for any innate skill). |
| Mock sites (in-place mock-line removal) | `tests/helpers/send_message_fixtures.py:68`, `tests/test_manager_shutdown.py:126` (`manager._shutdown_opencode_registry = AsyncMock()`), `tests/integration/test_spawn_default_unchanged.py:163` | Drop the now-dead `patch(...)` / `AsyncMock()` lines (the methods/imports they reference no longer exist post-Phase-C) |
| Mock-strip family (extended, convergence addendum + reconciliation flip) | 16-file classification per planner-independent-gate execution (read-only enumeration, 2026-10-13) + 2-file reconciliation flip (re-execution found the originally-class-(b) "sibling allowlists" rows were NOT allowlist-excluded by the sibling and the sibling's decomposition lists them as TOUCH — flipped to TOUCH here). **class (a) import/mock of opencode symbols → TOUCH C.4 rephrase — 12 files / 21 patch sites**: `test/packs/wc_wake_off_bytecompat_probe_test.py:293` (patch); `tests/test_council_tools.py:55` (patch); `tests/test_governor_integration.py:61, :955` (two patches); `tests/test_governor_recursion_acceptance_walk.py:336` (patch); `tests/test_spawn_team_members.py:58` (patch); `tests/test_spawn_instance_instructive_errors.py:26, :42, :50, :362, :368` (RECONCILIATION FLIP — formerly class (b) cross-reference; flipped to class (a) TOUCH rephrase: the strings `"opencode"` inside `@pytest.mark.skip` test bodies are plain editable strings; rephrase the agent-id string to a still-existing agent id (e.g. `"coder"`, `"developer"`, `"designer"`, `"watcher"`); behavior-neutral, tests stay skipped, mock_meta fixture still exercises `validate_agent_id` against a real agent id); `tests/tools/test_auto_kill_integration.py:992` (`manager._shutdown_opencode_registry = AsyncMock()`); `tests/tools/test_send_message_context_param.py:106` (patch); `tests/tools/test_send_message_load_skill.py:77` (patch); `tests/unit/test_governor_recursion_guard.py:170` (patch); `tests/unit/tools/test_spawn_councilor_default_version.py:126` (patch); `tests/unit/tools/test_version_tag_tool_resolution.py:432, :1104, :1292` (three patches). **class (b) NEGATIVE GUARD (asserts opencode ABSENCE — string is the assertion) → NOT yours; sibling allowlists (cross-reference only) — 2 files**: `tests/unit/test_devops_agent.py` (multiple `test_no_opencode_*` and `test_devops_does_not_have_opencode_tools` negative guards asserting DevOps has no opencode surface — passes even stronger post-C.2; sibling allowlists; not my edit); `tests/unit/test_reviewer_v2_agent.py:130-135` (`test_opencode_not_in_innate_skills` — D7 reviewer[v2] guard; passes after base==v2; sibling allowlists; not my edit). **class (c) stale comment/prose in test code → TOUCH C.4 cosmetic — 6 sites**: `tests/integration/test_persistence_w1_markers.py:15` (docstring "This test does NOT require a live OpenCode server — it only needs"); `tests/test_governor_recursion_acceptance_walk.py:35` (docstring "project / job / MCP / opencode / db / infra / context / chart tool"); `tests/test_spawn_team_members.py:42` (docstring "OpenCode"); `tests/tools/test_send_message_context_param.py:84` (docstring "OpenCode"); `tests/tools/test_send_message_load_skill.py:55` (docstring "OpenCode"); `tests/unit/tools/test_prompt_section_reference_integrity.py:815` (RECONCILIATION FLIP — formerly class (b) "integrity test's own self-test fixture" cross-reference; flipped to class (c) TOUCH cosmetic: the `LEGIT_SEE_SHAPE_FIXTURES` tuple's `("parenthetical", "- MEDIUM+ scope: 2-3 opencode sessions — run SEQUENTIALLY (one at a time, See Resource Constraint (STRICT))")` is an editable sample string; rephrase to a non-opencode sample (e.g. "2-3 background jobs — run SEQUENTIALLY (one at a time, See Resource Constraint (STRICT))"); behavior-neutral — any See-shaped sample works for the fixture). **Do NOT exclude `test_prompt_section_reference_integrity.py` from anything** — it IS the integrity sweep itself; excluding it would mask future leaks in it. | For class (a) patches/AsyncMocks: drop the now-dead `patch(...)` / `AsyncMock()` lines (the methods/imports they reference no longer exist post-Phase-C). For class (a) skipped-test strings: rephrase the agent-id string `"opencode"` to a still-existing agent id (e.g. `"coder"`, `"developer"`, `"designer"`, `"watcher"`); the tests stay `@pytest.mark.skip`; behavior-neutral. For class (c) docstrings: rephrase to a non-opencode string (e.g. "external services", "third-party integration", "background jobs", or simply drop the OpenCode word); behavior-neutral. For class (b) guards: NO edit; cross-reference to phaseE's allowlist file. |
| `pyproject.toml` | `:88` (`pytest` markers block — P3: rephrase, do NOT delete the marker registration row) | **Rephrase** the `integration` marker description: replace `"integration: integration tests that require a live OpenCode server (run with 'pytest -m integration')"` with a non-opencode description (e.g. `"integration: integration tests that require live external services (run with 'pytest -m integration')"`). 5+ files use `@pytest.mark.integration` (e.g. `tests/e2e/test_mcp_tools.py:47`); the row MUST stay. **Also (R3):** ADD `testpaths = ["tests"]` to `[tool.pytest.ini_options]`. Mechanism adjudication: `testpaths`, NOT `norecursedirs` (the latter REPLACES pytest's default ignore set — footgun; `testpaths` is one additive line and also stops today's accidental bare-`pytest` collection of `test/packs/*.py`). Without this, a bare `pytest` from repo root post-C.3 will collect `legacy/tests/opencode/**` (12 `test_*.py` + conftest) → `ModuleNotFoundError` storm. |
| `.env.example` | `:39` | Rephrase the `# OPENAI_MODEL_KEYWORDS=quick` block-comment to drop the "outgoing opencode prompts" wording (replace with "outgoing prompts"; the env var and its purpose stay since `daemon/services/keyword_extraction.py` is live-shared infra per D5) |
| Comment / docstring / log-string rephrases (R10 — MANDATORY, not optional) | `daemon/config.py:168`; `daemon/services/keyword_extraction.py` (module docstring line 1 + line 9 + log lines :426 + :430; module STAYS per D5 — wording mandatory, code untouched); `daemon/services/context_injection.py:541,846`; `daemon/services/context_messages.py:35,39`; `daemon/services/critical_note_gate.py:8`; `daemon/services/waiting_children_watchdog.py:9,752`; `daemon/loader.py:226`; `daemon/repositories/task/repository.py:1777` (D5 "may not churn" hesitation is SUPERSEDED for this comment-only cite — code untouched, rephrase is mandatory) | Replace opencode-mention examples with non-opencode strings or strip the comment. Behavior-neutral edits. R10 upgrades Tier-2's earlier "optional / if unsure LEAVE" framing to a mandatory-fix set. |
| Former-P7 cosmetic stragglers (R10 — now MANDATORY) | `daemon/tools/instance.py:5823` and `:5860` (trailing comments) + `:6246` (trailing comment) + grant-map header `:155-156`; `tests/job_queue/conftest.py:65`; `tests/unit/services/test_critical_notes_phase2_filters.py:11` (docstring "daemon/tools/external_opencode.py — list-and-coerce path"); `tests/integration/test_auth_boot_preflight.py:20`; `test/packs/lcan_helpers/mock_llm.py:141-142`; `test/packs/regression_integration_test.sh:31` and `:40`; `test/packs/regression_chat_source_integration_test.sh:8` | Rephrase or drop the opencode-mention. Behavior-neutral. |
| Cosmetic one-liners | `agents/explorer/soul.md`, `agents/devops/soul.md`, `agents/_prompt_system/knowledge.md:96,106` (verified present — live prompt-system prose every agent reads), **`agents/maintenancer/knowledge/01-architecture-overview.md` (B3 — agent-facing surface; NOT allowlist-eligible)** | Rephrase or drop the opencode-mention one-liners; no test relies on these strings. For `01-architecture-overview.md` specifically, two opencode lines in the live tree (verified read-only): **`:6` (line in "Facade + registry" section: `daemon/manager.py` coordinates lifecycle, repos, queues, sources, **MCP/OpenCode**, tools, LangGraph)** and **`:16` (line in "Checkpoint + CLE" section: `daemon/checkpoint_adapter.py` normalizes LangGraph checkpoints; **OpenCode keeps its own SQLite**)** — rephrase to drop the `OpenCode` references while preserving the architectural prose. The file has 22 lines total; only these 2 carry the opencode mention. Behavior-neutral; no test relies on these strings; no other opencode refs in the file. |

(Note: the previous "Tier-1 / Tier-2 / Tier-3 / Tier-4 / Tier-5" lists in §4 are superseded by this consolidated MOVE / TOUCH / LEAVE taxonomy. The list above IS the final edit surface. R1, R2, R3, R5, P1, P2, P3, P8, R10 additions captured in this fold pass; pre-fold omissions are closed. The "comment-rephrases" row was previously marked "optional / if unsure LEAVE" — R10 SUPERSEDES that framing: it is now a mandatory-fix set per architect directive. Former-P7 list now mandatory in C.4.)

### 13.6 LEAVE ALONE (final — explicitly NOT touched by this phase)

| Path | Reason | Citation |
|---|---|---|
| `agents/coder/` | OPENCODE-FREE BY DESIGN; contamination guards stay green | addendum (a) + `tests/unit/test_coder_agent.py` guards |
| `daemon/services/keyword_extraction.py` (module) | LIVE-SHARED infra; D5 + active non-opencode callers | D5, addendum (f), `attestation_report_judge.py:35-328`, `graph.py:10794`, `plugin_subsystem/opendesign/generate.py:650` |
| `daemon/services/context_injection.py` (module) | LIVE-SHARED infra; active non-opencode callers | addendum (f) + `daemon/services/context_injection.py:541,846` is the comment-only mention rephrased in TOUCH, the module itself stays |
| `docs/mcp-kb-usage.md` | D6 false positive (upstream `sst/opencode` product, not our service) | D6 binding |
| `.agents/*/RESULTS/`, `.agents/*/LESSONS/` | Tier-5 historical records | binding rule |
| `data/` / `data_dev/` SQLite DB files (`opencode_sessions.db` and any stray DB at repo root) | Boot-recreated state; D7. The DB-creation code dies with `daemon/opencode/repository.py:313` removal — no more writes. Pre-existing repo-root stray DB is an orphan test artifact; **NOTE ONLY**, do not delete as part of this phase (cardinal rule: do not silently mutate shared-state artifacts) | D7 + addendum |
| `.inspiration-projects/opencode_skill_src/` | `.gitignore:71` already excludes this directory (`grep -n 'inspiration-projects' .gitignore` returns line 71); gitignored, never tracked, no work | `.gitignore:71` |
| User-facing docs (`docs/agents.md` §2, `docs/skill-evolution.md:174`, `README.md`, `CHANGELOG.md`, `AGENTS.md`) | Phase D owns these — phase C does not edit | phaseD binding |
| Frontend (`frontend/src/app/pages/jobs/jobs.component.ts:2389` and any other frontend pin) | Note-only; verify-only | binding rule |
| Name pins (`TOOL_REQUIRED_AGENTS`, `REPORT_REPAIR_EXCLUDED_AGENTS`, `default_agent_versions`, attestation leader refs, default `"ari"` × 5 pins, spawn-by-name sites) | Verified safe (no opencode tokens to churn) — R7: "attestation leader refs" is `daemon/services/instance_lifecycle.py:2305,4753` referring to the leader AGENT (not a `[v2]` pair) | binding rule |

### 13.7 ORDERING CONSTRAINT — Phase-B-coupled gate (engineering-critical)

**Binding:** Stack removal and base:=v2 retirement are COUPLED via the `daemon/loader.py:226`-style innate-skill loader (R4 — `daemon/services/inheritance/loader.py` does NOT exist; the real loader is `daemon/loader.py`; the warning path is at `daemon/loader.py:389` for missing skill dirs): **while the 5 old-base agents STILL declare `["opencode", "chart", "todo"]` in their `meta.json`, the `agents/_prompt_system/innate-skills/opencode/` directory AND the `INNATE_SKILL_TOOL_CATEGORIES["opencode"]` map entry MUST still resolve** for those agents' cold-start to not regress on the "skill not found" / "no tool categories for this innate skill" paths. Phase C's removal of `daemon/opencode/`, `agents/_prompt_system/innate-skills/opencode/`, and the grant-map entry lands **WITH or AFTER Phase B (base := v2 retirement)** — NEVER BEFORE.

**Restructured commit-checkpoint sequence (replaces §5 Steps 1-5):**

#### Step 0 — Pre-flight (no commit)
- Verify Phase A archive integrity (`sha256sum legacy/...` matches working tree, byte-identical).
- Verify Phase B base-copy diff is empty (`diff -r agents/<base> agents/<base>[v2]` returns nothing for all 5 pairs + planner).
- Read VERDICT 2 (this section §13.2) and confirm BRANCH 1 selection in writing before any commit. If executor doubts the verdict — STOP, report to dispatcher.
- Set up the fenced dev-lane env (worktree `ENSEMBLE_SELF_ENV=dev` + ensemble_dev DB + PORT 8079 + worktree-local `data/`).

#### Commit C.1 — Stack-coupling-independent TIER-1 WIRING SEVERANCE (boot clean)

This commit severs the wiring that does NOT depend on the Phase-B base-retirement, in `daemon/manager.py`, `daemon/tools/instance.py`, `daemon/tools/_tool_registry.py`, and `Makefile`. The orphan source files (`daemon/opencode/`, `daemon/tools/external_opencode.py`, `agents/_prompt_system/innate-skills/opencode/`) remain on disk after this commit because those removals are COUPLED to Phase B and land in a later commit.

Edits:
- `daemon/manager.py` — strip :78 import; strip :1420-1436 init; strip :2895-2910 accessor (and surrounding comment header); strip :3325-3338 (the commented-out recovery block, in case it's been uncommented); strip :5948-5956 idle-eviction block; strip :6750-6751 jsonb DO-block rows; strip :13033 (shutdown-tuple entry) AND :13078-13103 (`_shutdown_opencode_registry` method body) together.
- `daemon/tools/instance.py` — strip :155-167 grant-map row for `"opencode"`; strip :259 import; strip :5815-5818 factory call block + `tools.extend(...)` + comment header.
- `daemon/tools/_tool_registry.py` — strip :603 module-map row; strip :764-771 KNOWN_TOOL_NAMES rows. **All three sites (instance.py grant-map, factory call, KNOWN_TOOL_NAMES) land in the same commit per risk #2.**
- `Makefile` — strip `opencode_sessions.db` + WAL/SHM siblings from the `FILES=` backup list.

**Boot pre-condition for C.1:** Phase B must have already removed `["opencode", ...]` from the 5 base agents' `meta.json` files. Otherwise, after C.1 lands, those 5 base agents (if user-spawned) would see graceful-fallback "no prompt found" at the opencode-skill loader. After Phase B, no agent carries `["opencode", ...]` in `innate_skills`, so C.1 does NOT regress any agent's cold-start.

Boot proof (fenced dev lane; do NOT boot main checkout):

**S4 pre-flight (BEFORE boot attempt):** confirm the `ensemble_dev` DB is reachable from the worktree's fenced dev lane (worktree-local data pins; ENSEMBLE_SELF_ENV=dev; PORT=8079; ENSEMBLE_POSTGRES_DB=ensemble_dev). Recommended probe:
```
# Verify DB connectivity (any of these — adapt to the worktree's local DB driver)
PGPASSWORD=$ENSEMBLE_POSTGRES_PASSWORD psql -h localhost -U $ENSEMBLE_POSTGRES_USER -d ensemble_dev -c "SELECT 1;" 2>&1 | head -5
# OR, if the worktree uses SQLite for dev:
[ -f "$WORKTREE/data/ensemble_dev.db" ] && echo "DB present" || echo "DB MISSING — initialize before boot"
# OR, if the worktree uses the dev-orchestrator's auto-init:
curl -sf http://localhost:8079/__db_health || echo "DB health endpoint not reachable yet"
# If unreachable: STOP. The fenced dev lane's boot attempt will fail at the first DB-touching call.
```

Then proceed to:
- `ENSEMBLE_SELF_ENV=dev PORT=8079 ./dev.sh boot` (verify invocation at use time)
- `curl /livez` + `/readyz` → 200
- `grep -c 'opencode' data/logs/*.log` → 0 (modulo D5/D6/Tier-5 false-positives; see DoD §9.15)
- `kill -TERM <pid>` → clean shutdown, no traceback from missing `_shutdown_opencode_registry`

Commit message prefix: `[agent-base-v2/C/1] chore(daemon): tier-1 opencode wiring severance`

#### Step 1.5 — B-coupled PIVOT CHECK (no commit; BLOCKING GATE)

This is the explicit gate from the ORDERING CONSTRAINT in the addendum. Before commit C.2 lands, the executor MUST verify in writing:

```
# Phase B base-copy diff is empty (BLOCKING):
for pair in developer reviewer tidier approver planner; do
  diff -r "agents/$pair" "agents/${pair}[v2]" || { echo "BASE COPY NOT EMPTY FOR $pair — STOP"; exit 1; }
done
```

If any `diff` is non-empty OR Phase B was skipped → STOP. Do not proceed to C.2-C.5. Report to dispatcher.

If all `diff` empty AND Phase B is in the worktree's git history → proceed to C.2.

#### Commit C.2 — Stack-removal (coupled to Phase B; boot clean)

This commit removes the source-tree artifacts that C.1's wiring severance leaves orphaned. The single commit keeps the import graph and the disk state aligned — no intermediate commit imports a removed module. R4 clarified: C.2 is `git rm`-only. The `INNATE_SKILL_TOOL_CATEGORIES` grant-map row, the `from .external_opencode import` line, the factory-call block, the `_tool_registry.py` module-map row, the 8 `KNOWN_TOOL_NAMES` rows, the `daemon/manager.py` Tier-1 sites, and the `Makefile` FILES list all land in C.1 — none are held back. The genuinely Phase-B-coupled agent-visible edit that the gate fronts is the **prompt-directory removal** (`agents/_prompt_system/innate-skills/opencode/`), which would leave base agents' `["opencode", …]` meta.json with a missing skill-prompt path if Phase B has not yet landed.

Edits + removals (single commit, git-rm-only):
- `git rm` `daemon/opencode/{__init__, client, constants, registry, repository, server, session_manager, state}.py`
- `git rm` `daemon/tools/external_opencode.py`
- `git rm` `agents/_prompt_system/innate-skills/opencode/skill.md` + `skill.md.bak`
- (No further daemon code changes — the wiring was already severed in C.1. No Makefile changes — already in C.1.)

Boot proof (same fence):
- Re-boot fenced dev lane. Confirm `/livez` + `/readyz` 200. Confirm log scan returns no opencode boot/load errors. Clean shutdown.

Commit message prefix: `[agent-base-v2/C/2] chore: full-stack opencode source-tree removal (coupled to base := v2)`

#### Commit C.3 — Test-pack + planner-doc + bug-doc ARCHIVAL (legacy mirror; R6: move-only, no test-invariant)

`git mv` to `legacy/` mirror per Phase A's layout, no semantic content changes:

- `git mv tests/opencode/ legacy/tests/opencode/` (12 files)
- `git mv test/packs/opencode_native_tools_unit_test.sh legacy/test/packs/`
- `git mv test/packs/ab/top_opencode_unit_test.sh legacy/test/packs/ab/`
- `git mv test/packs/regression_opencode_e2e_test.sh legacy/test/packs/`
- `git mv docs/bugs/external-opencode-wait-latency.md legacy/docs/bugs/`
- `git mv docs/bugs/opencode-session-manager-memory-leak.md legacy/docs/bugs/`
- `git mv .agents/shared/planning/opencode-native-tools/ legacy/.agents/shared/planning/opencode-native-tools/` (7 docs)
- `git mv example/SKILL.md legacy/example/SKILL.md` (Go-binary contract spec; archive only — historical reference)

Note: `tests/test_innate_skills_refactoring.py` and `tests/migration/test_jsonb_migration.py` are NOT moves (see C.4); they stay in place and are edited.

**R6 invariant declaration for C.3:** move-only commit, **NO test-green gate** at this boundary. The C.3 zero-grep gate that the prior draft listed at this step is moved to C.4 (C.3 still has hits at `tests/test_innate_skills_refactoring.py:11,:14,:59-65,:85,:94,:98,:227,:234-236,:271,:275,:280,:297-301,:353`, `tests/migration/test_jsonb_migration.py:68-69,:75,:106-107,:194-197,:223-226,:366-367,:497,:518`, `tests/test_tool_filter.py:710-716,:750-764,:827-860`, and the **expanded mock-strip family (16 files, post-convergence-addendum + 2-file reconciliation flip; see §13.5 "Mock-strip family (extended)" row)**: `test/packs/wc_wake_off_bytecompat_probe_test.py:293`, `tests/test_council_tools.py:55`, `tests/test_governor_integration.py:61,:955`, `tests/test_governor_recursion_acceptance_walk.py:336`, `tests/test_spawn_team_members.py:58`, `tests/test_spawn_instance_instructive_errors.py:26,:42,:50,:362,:368` (RECONCILIATION FLIP — was class (b) cross-reference, now class (a) TOUCH), `tests/tools/test_auto_kill_integration.py:992`, `tests/tools/test_send_message_context_param.py:106`, `tests/tools/test_send_message_load_skill.py:77`, `tests/unit/test_governor_recursion_guard.py:170`, `tests/unit/tools/test_spawn_councilor_default_version.py:126`, `tests/unit/tools/test_version_tag_tool_resolution.py:432,:1104,:1292` — all edited in C.4; plus the 6 cosmetic-docstring/prose sites (5 original + 1 reconciliation-flip): `tests/integration/test_persistence_w1_markers.py:15`, `tests/test_governor_recursion_acceptance_walk.py:35`, `tests/test_spawn_team_members.py:42`, `tests/tools/test_send_message_context_param.py:84`, `tests/tools/test_send_message_load_skill.py:55`, `tests/unit/tools/test_prompt_section_reference_integrity.py:815` (RECONCILIATION FLIP — was class (b) "integrity test self-test fixture" cross-reference, now class (c) TOUCH cosmetic) — all edited in C.4). The 2 negative-guard files (`tests/unit/test_devops_agent.py`, `tests/unit/test_reviewer_v2_agent.py:130-135`) are NOT in C.4 edit surface — sibling allowlists in phaseE's gate. After C.3 lands but before C.4, a bare `pytest` from repo root will FAIL with collection errors (`tests/migration/test_jsonb_migration.py` import error from `:75`); the mission's own commands are explicit-path (`pytest tests/`) so this is not mission-blocking, but R3 (testpaths) closes the dev-workflow landmine in C.4.

Commit message prefix: `[agent-base-v2/C/3] test+docs: archive opencode-specific tests and historical docs to legacy/`

#### Commit C.4 — In-place test edits + comment + cosmetic + manifest cleanups (R6: FIRST test-green commit)

In-place edits only (the R6 zero-grep gate lands at this commit):

- `tests/test_innate_skills_refactoring.py` — replace "opencode" fixture strings (16 P1 sites: `:11,:14,:56,:59-65,:84-85,:94,:98,:227,:232-236,:241,:271,:275,:280,:287-289,:297-301,:353`; plus 3 R1 sites in `test_tool_filter.py` + 8 R2 sites in `test_jsonb_migration.py`; total **27 cite locations across R1+R2+P1**) with another innate-skill (e.g. `"chart"` / `"test-skill"`); preserve the suite's intent (asserts the loader works for any skill name)
- `tests/migration/test_jsonb_migration.py` (R2 — full-file opencode surface) — drop `:68-69` opencode comment; drop `:75` module-level `from daemon.opencode.repository import OpenCodeSessionRecord` import (breaks pytest COLLECTION for the whole file post-C.2; this is the highest-impact line in the file); drop `:106-107` 2 `("opencode_sessions", …)` rows from `JSONB_MIGRATION_PAIRS`; update `assert len == 17` → `assert len == 15`; drop/rewrite `:194-197` + `:223-226` fixture bodies calling `OpenCodeSessionRecord.__table__.create(...)`; edit `:366-367` to drop the 2 `opencode_sessions` rows from `_DO_BLOCK_SQL` (mirrors `manager.py:6750-6751` removal); drop `:497` method-scoped import; drop `:518` `"opencode_sessions": OpenCodeSessionRecord` row from `table_to_class`; update the inline comment to reflect 15 pairs
- `tests/test_tool_filter.py` (R1) — rephrase the 3 red tests at `:710-716`, `:750-764`, `:827-860` to a surviving skill (e.g. `"chart"` / `"test-skill"`); deny test `:862-887` and no-op tests `:707, :717-722` pass coincidentally — no edit
- `tests/unit/test_coder_agent.py:40-43` — drop the `"external_opencode"` mirror entry from the expected tool-set fixture; KEEP the contamination guards at `:144`, `:168`, `:369+`, `:510`, `:253` untouched (they keep PASSING — that's the contract)
- `tests/unit/test_wanderer_agent.py:41-44` (P2) — same mirror edit as coder's `:40-43`
- `tests/helpers/send_message_fixtures.py:68`, `tests/test_manager_shutdown.py:126`, `tests/integration/test_spawn_default_unchanged.py:163` — drop the dead `patch(...)` / `AsyncMock()` lines
- `pyproject.toml` (P3 + R3) — at `:88` REPHRASE the `integration` marker description (do NOT delete the registration row; 5+ files use `@pytest.mark.integration`); ALSO add `testpaths = ["tests"]` to `[tool.pytest.ini_options]` (R3 — `testpaths`, NOT `norecursedirs`)
- `Makefile:173-174, :186` (R5) — rephrase the `:173-174` comment to drop the "opencode_sessions.db (always a local SQLite file, never migrated to postgres)" wording AND drop the `opencode_sessions.db` + WAL/SHM siblings from `:186` FILES=
- `.env.example:39` — drop "outgoing opencode prompts" wording from the `OPENAI_MODEL_KEYWORDS` block-comment; the env var STAYS (D5 + addendum (f))
- Comment rephrases (R10 — MANDATORY, not optional) — 9 files: `daemon/config.py:168`, `daemon/services/keyword_extraction.py` (docstring + log lines; module STAYS per D5), `daemon/services/context_injection.py:541,846`, `daemon/services/context_messages.py:35,39`, `daemon/services/critical_note_gate.py:8`, `daemon/services/waiting_children_watchdog.py:9,752`, `daemon/loader.py:226`, `daemon/repositories/task/repository.py:1777`. D5 "may not churn" hesitation is SUPERSEDED for this comment-only cite (R10).
- Former-P7 cosmetic stragglers (R10 — now MANDATORY) — 8 sites: `daemon/tools/instance.py:5823,:5860,:6246` trailing comments + grant-map header `:155-156`; `tests/job_queue/conftest.py:65`; `tests/unit/services/test_critical_notes_phase2_filters.py:11`; `tests/integration/test_auth_boot_preflight.py:20`; `test/packs/lcan_helpers/mock_llm.py:141-142`; `test/packs/regression_integration_test.sh:31,:40`; `test/packs/regression_chat_source_integration_test.sh:8`
- Cosmetic one-liners (3 files): `agents/explorer/soul.md`, `agents/devops/soul.md`, `agents/_prompt_system/knowledge.md:96,106`

**R6 zero-grep gate (moved from C.3 to C.4):** after C.4, run `grep -rn 'opencode_sessions\|create_opencode_tools\|external_opencode\|OpenCodeSession' daemon/ tests/ agents/ Makefile pyproject.toml .env.example --include='*.py' --include='*.sh' --include='*.md' --include='*.toml' --include='Makefile' --exclude-dir=legacy --exclude-dir=.agents/tester/RESULTS --exclude-dir=.agents/tester/LESSONS --exclude=docs/mcp-kb-usage.md` and confirm ZERO hits (modulo D5/D6/D7/Tier-5/legacy exclusions).

**R6 boot proof:** full `python -m pytest tests/ --timeout=120` in fenced dev lane. C.4 is the **FIRST test-green commit** in the sequence; C.1/C.2 are boot-green only (R6 invariant); C.3 has no invariant.

**§5 preamble alignment (R6):** the "bootable at every checkpoint" framing remains accurate — the test-green deferral to C.4 is now EXPLICIT. Per-commit invariants:

| Commit | Boot-green | Test-green | Notes |
|---|---|---|---|
| C.1 | YES | NO | 15 mock sites + R1 test_tool_filter.py 3 tests go runtime-red; import graph intact (per convergence addendum 2026-10-13, the expanded 16-file mock-strip family + 5 cosmetic-docstring sites are all C.4 edits — see §13.5 "Mock-strip family (extended)" row) |
| C.2 | YES | NO | tests/opencode/ collection ImportError until C.3; test_jsonb_migration.py collection ImportError (`:75`) + test_innate_skills_refactoring.py fixtures go red until C.4 |
| C.3 | (no boot test) | NO | move-only; bare `pytest` collection errors persist until C.4 (R3 testpaths fixes bare-pytest landmine in C.4) |
| C.4 | YES | **YES (FIRST)** | R6 zero-grep gate lands; full `python -m pytest tests/ --timeout=120` must pass |

Commit message prefix: `[agent-base-v2/C/4] test+docs: in-place opencode fixture refresh + comment rephrases (FIRST test-green)`

#### Commit C.5 — Evidence artifact (no code)

- Write `.agents/shared/planning/agent-base-v2/evidence/phaseC-boot-proof.md` carrying: pre-C worktree SHA + post-C worktree SHA + the 4 commit SHAs (C.1-C.4) + Phase A sha-equality proof + Phase B diff-emptiness proof + fenced dev-lane boot transcript (`/livez`+`/readyz`+log scan=0) + clean-shutdown sequence + `pytest` final line + final grep-sweep classification table (D5/D6/Tier-5/legacy excluded).
- If `.agents/shared/planning/...` is git-tracked: `git add` + commit normally. If gitignored: leave as local evidence; the verification-pack handoff to Phase E is by file-path reference in the C.4 commit message.

Commit message prefix (if committed): `[agent-base-v2/C/5] evidence: phase-c boot proof artifact`

### 13.8 Final grep-clean sweep criteria (updated — supersedes §10)

Phase C's local grep-clean sweep gate lands at the end of C.4 (R6). The final WHOLE-TREE active-tree gate is **Phase E's R10 tester gate** (architect §11.3, expanded by fix-pass W4 + B1 fix-pass 3) — cross-reference, do not duplicate.

**W4 + B1 update (fix pass 2 + fix pass 3):** the dry-run at HEAD showed the R10 gate as previously written could not reach zero (~1,600 matching lines; 1,346 under `.agents/` alone; `.kilo` = 66 lines not "3 files"). The exclusion set is now ALIGNED cross-file with phaseE Step 13's command (the sibling worker is updating phaseE's command in lockstep; the COMPLETE aligned list is mirrored here, with phaseE as the single source of truth for the verbatim command — phaseC only carries the inline set + allowlist row format for cross-file consistency).

| Local Phase C gate (C.4 boundary; this phase emits) | Phase E R10 tester gate (post-mission; single source of truth = `phaseE-verification-evidence.md` Step 13) |
|---|---|
| `grep -rn 'opencode_sessions\|create_opencode_tools\|external_opencode\|OpenCodeSession' daemon/ tests/ agents/ Makefile pyproject.toml .env.example --include='*.py' --include='*.sh' --include='*.md' --include='*.toml' --include='Makefile' --exclude-dir=legacy --exclude-dir=.agents/tester/RESULTS --exclude-dir=.agents/tester/LESSONS --exclude=docs/mcp-kb-usage.md` | `git grep -niE 'opencode' -- . \  ':(exclude)legacy' \  ':(exclude)CHANGELOG.md' \  ':(exclude)docs/incidents' \  ':(exclude)docs/incidents/**' \  ':(exclude).agents/shared/planning' \  ':(exclude).agents/shared/planning/**' \  ':(exclude).agents/*/RESULTS*' \  ':(exclude).agents/*/LESSONS*' \  ':(exclude).agents/*/memories' \  ':(exclude).agents/*/memories/**' \  ':(exclude).agents/*/working' \  ':(exclude).agents/*/working/**' \  ':(exclude).agents/*/EVIDENCE*' \  ':(exclude).agents/tester/PACKS.md' \  ':(exclude).agents/approver/active.md' \  ':(exclude).agents/approver/agent-base-v2-tracking.md' \  ':(exclude).kilo' \  ':(exclude).slim' \  ':(exclude).slim/**' \  ':(exclude)backup' \  ':(exclude)backup/**' \  ':(exclude)LESSONS' \  ':(exclude)LESSONS/**' \  ':(exclude)docs/plans' \  ':(exclude)docs/plans/**' \  ':(exclude)docs/test-suite-known-issues.md' \  ':(exclude)plugins/opendesign/copy_freely/design-systems/opencode-ai/**' \  ':(exclude)plugins/opendesign/copy_freely/HASHES.sha256' \  ':(exclude)docs/mcp-kb-usage.md' \  ':(exclude)docs/bugs' \  ':(exclude)docs/bugs/**'` (case-insensitive — subsumes external_opencode, OPENCODE_URL, opencode_sessions — no separate patterns needed). |

**Each expanded exclusion requires an allowlist row + justification in `evidence/r10-allowlist.md` (B1 / B3 — agents/maintenancer/knowledge/** and docs/mcp-integration.md are NOT in the exclusion list — they are FIXED, NOT allowlist-eligible):**

| Exclusion | Allowlist justification (one row per Phase E) |
|---|---|
| `legacy` / `legacy/**` | Archived opencode-stack copies (MOVE set, copy-then-verify per Phase A) — preserved-but-unused; not active-tree. |
| `CHANGELOG.md` | Project changelog; historical record of opencode-era entries. |
| `docs/incidents` / `docs/incidents/**` | Historical incident records (Tier-5 historical; not active). |
| `.agents/shared/planning` / `.agents/shared/planning/**` | Planning history (this `agent-base-v2/` directory, `opencode-native-tools/`, etc.) — historical planning documents; not active-tree. |
| `.agents/*/RESULTS*` | Test result records (e.g., `.agents/tester/RESULTS/2026-07-30-tester-opencode-removal-verification.md`) — Tier-5 historical; already-archived test outcomes. |
| `.agents/*/LESSONS*` | Test lessons (e.g., `.agents/tester/LESSONS/phase-b-opencode-filesystem-path-issue.md`) — Tier-5 historical. |
| `.agents/*/memories` / `.agents/*/memories/**` | Agent memory files (e.g., `.agents/leader/memories/`, `.agents/tester/memories/`) — historical agent-state records; not active configuration. |
| `.agents/*/working` / `.agents/*/working/**` | Agent working/ scratch directories — historical scratch state; not active-tree. |
| `.agents/*/EVIDENCE*` | EVIDENCE/ subdirectories (e.g., `.agents/tester/EVIDENCE/`) — historical evidence records; not active. |
| `.agents/tester/PACKS.md` | Tester-pack index referencing opencode-named packs (`PACKS.md:922,:957,:1272,:2157`) — historical pack metadata; not an active surface. |
| `.agents/approver/active.md` | Approver identity file (current plan name, slug, iteration number) — meta-state, not active configuration. |
| `.agents/approver/agent-base-v2-tracking.md` | Approver tracking file (this mission's iteration history) — meta-state, not active configuration. |
| `.kilo` | 3 tracked files (residue from kilo-tool historical state) — executor classifies at gate time (historical tool state → allowlist; active config → FIX). |
| `.slim` / `.slim/**` | `.slim/cartography.json` and related — historical tool state. |
| `backup` / `backup/**` | Pre-existing backup copies of historical agent dirs (e.g., `backup/agents/tester/meta.json` with `innate_skills: ["opencode"]`) — historical archive; not active-tree. |
| `LESSONS` / `LESSONS/**` | Repo-root LESSONS/ directory — historical lessons; not active. |
| `docs/plans` / `docs/plans/**` | Planning docs in docs/plans/ — historical planning documents (already covered by R10 mandatory-fix in Phase D for active rows; allowlist covers any historical residue). |
| `docs/test-suite-known-issues.md` | Test suite known-issues doc — historical record. |
| `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` | Vendored third-party design-system resource (provenance class `copy_freely`; shipped via `stage.sh:407`) — NOT a legacy move; line-by-line strictly-upstream verify + allowlist row. |
| `plugins/opendesign/copy_freely/HASHES.sha256` | Vendored content-hash manifest for the third-party design-system (24 opencode-ai rows) — NOT excluded by the `opencode-ai/**` directory pattern because it's a sibling file; allowlist row required. |
| `docs/mcp-kb-usage.md` | Upstream `sst/opencode` product docs (D6) — line-by-line strictly-upstream verify + allowlist row. |
| `docs/bugs` / `docs/bugs/**` (conditional) | The two `external-opencode-wait-latency.md` + `opencode-session-manager-memory-leak.md` were moved to `legacy/docs/bugs/` in C.3; any per-file "historical incident record" rows the executor justifies in the allowlist file; bug docs describing CURRENT active-stack behavior must be FIXED, not excluded. |

**NOT in the exclusion list (B1 / B3 — these are FIXED, NOT allowlist-eligible):**

- **`agents/maintenancer/knowledge/**`** — live opencode refs at `agents/maintenancer/knowledge/01-architecture-overview.md:6, :16`; added to Phase C TOUCH set (B3); C.4 mandatory-fix.
- **`docs/mcp-integration.md`** — live opencode refs at `:184-195` and other sites; the sibling worker (Phase D) owns the MANDATORY-FIX per B2.

**Phase E Step 13 is the single source of truth for the verbatim R10 gate command** (the command itself is being aligned in `phaseE-verification-evidence.md` and `architecture-recommendation.md §11.3` by the sibling worker in lockstep with this §13.8 cross-reference). Phase C's §13.8 only carries the expanded exclusion set + allowlist row format; the executable command lives downstream.

Phase C's C.4 local-gate categories:

| Category | Pattern | Expected | Reason |
|---|---|---|---|
| EXPECTED_LEAVE (D5) | `daemon/services/keyword_extraction.py`, `daemon/services/context_injection.py` (modules per D5) | matches allowed | live-shared infra |
| EXPECTED_LEAVE (D6) | `docs/mcp-kb-usage.md` | matches allowed | upstream sst/opencode product, not our service |
| EXPECTED_LEAVE (D7) | `data*/opencode_sessions.db`, `*.db-wal`, `*.db-shm`, any orphan `.db` at repo root | matches allowed | boot-recreated state; orphan DBs are NOTE only |
| EXPECTED_LEAVE (Tier-5) | `.agents/*/RESULTS/**`, `.agents/*/LESSONS/**` | matches allowed | historical records |
| EXPECTED_LEAVE (legacy/) | `legacy/daemon/opencode/**`, `legacy/daemon/tools/external_opencode.py`, `legacy/agents/_prompt_system/innate-skills/opencode/**`, `legacy/tests/opencode/**`, `legacy/docs/bugs/{external-opencode-wait-latency,opencode-session-manager-memory-leak}.md`, `legacy/test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}`, `legacy/.agents/shared/planning/opencode-native-tools/**`, `legacy/example/SKILL.md` | matches allowed | archived mirror |
| EXPECTED_LEAVE (Phase D scope) | `docs/agents.md` §2, `docs/skill-evolution.md:174`, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/test-suite-known-issues.md`, `docs/plans/*`, `docs/optimization/*`, `docs/mcp-integration.md`, `agents/{developer,reviewer,tidier,approver,planner}/**` opencode-comment-in-prose mentions, `agents/leader/**`, `agents/coder/**` (NB: coder IS opencode-free by design; guards stay green), `backup/agents/**`, `LESSONS/**`, `.slim/cartography.json` | matches allowed | Phase D / history |
| EXPECTED_LEAVE (third-party) | `plugins/opendesign/copy_freely/design-systems/opencode-ai/**`, `example/SKILL.md` (NOTE: archived to `legacy/` per addendum, but the working-tree copy is gone — verify), `.inspiration-projects/opencode_skill_src/**` (gitignored) | matches allowed | third-party / gitignored |
| EXPECTED_LEAVE (D5 cosmetic + TOUCH rephrased) | `daemon/services/keyword_extraction.py`, `daemon/services/context_injection.py` opencode mention rephrased (now referencing "outgoing prompts" not "outgoing opencode prompts"), comment-rephrase files in TOUCH set | rephrased | comment rephrases landed in C.4 |
| ACTIONABLE NEW (must-fix-now) | any match NOT in the EXPECTED_LEAVE categories above | 0 | grep sweep target |

The addendum-supplied evidence list (a)–(f) maps to EXPECTED_LEAVE rows: (a) coder stays; (b) base agents post-B have NO opencode in innate_skills; (c) manager wiring was passive; (d) no MCP configs; (e) port 4095 idle; (f) D5 keyword_extraction + context_injection modules stay.

### 13.9 Risks (S2 — aligned with the canonical plan-overview register rows 1-19)

**S2 fix-pass:** §13.9 now mirrors the canonical risk register in `plan-overview.md` (rows 11-19 are the architect-attributed Phase C risks; rows 14, 15, 19 are cross-referenced to their governing phase). The pre-existing local risks (worktree dev.sh drift, parallel-worker branch races, stray `.db` artifacts, markers-filter narrowing) that appeared in earlier §13.9 drafts are NOT in the plan-overview register and have been DROPPED — the pre-existing Risk #13 (verdict evidence drift) is DEDUPED (covered by plan-overview #10 "Executor evidence contradicts VERDICT 2"; see also Risk #19 below for the active-tree verification).

The headline risk (risk #1) is unchanged in spirit but its specification is sharpened:

- **Risk #1 (headline) — Phase-B-coupled execution window.** Stack removal is COUPLED to Phase B base-retirement via the innate-skill loader. **NEVER land C.2 before Phase B has been verified `diff -r` empty.** Mitigation: explicit Step 1.5 pivot check (BLOCKING GATE) before any C.2 commit; if Phase B status is unknown, STOP and ask.
- **Risk #11 (architect, R1) — `tests/test_tool_filter.py` 3 tests go RED at C.1.** Three tests assert opencode innate-skill expansion (`:710-716` `test_opencode_innate_skill_adds_external_opencode_category`, `:750-764` `test_multiple_innate_skills`, `:827-860` `test_apply_tool_filter_grants_opencode_tools_for_innate_skill`) and the C.1 grant-map-row strip in `daemon/tools/instance.py:155-167` makes them go runtime-red. The deny test `:862-887` and no-op tests `:707, :717-722` pass coincidentally — no edit needed. Mitigation: rephrase to a surviving skill in C.4 (the 3 red tests stay red from C.1 through C.3; C.4 is the first test-green commit per R6).
- **Risk #12 (architect, R2) — `tests/migration/test_jsonb_migration.py` collection ImportError post-C.2; opencode surface extends beyond `:70-107`.** The module-level import at `:75` (`from daemon.opencode.repository import OpenCodeSessionRecord`) breaks pytest COLLECTION for the entire file post-C.2; additional sites `:194-197`/`:223-226` (fixture bodies calling `OpenCodeSessionRecord.__table__.create(...)`), `:497` (method-scoped import), `:518` (`"opencode_sessions": OpenCodeSessionRecord` in `table_to_class`), `:366-367` (embedded `_DO_BLOCK_SQL` rows mirroring `manager.py:6750-6751`), `:68-69` (comment) all break at runtime. Mitigation: full-file opencode surface enumeration in §13.5 + C.4 in-place edits; executor MUST re-grep the file at execution time (sites drift) to surface any new opencode mentions added since this plan was written.
- **Risk #13 (architect, R3) — bare `pytest` post-C.3 collects `legacy/tests/**` → `ModuleNotFoundError` storm.** The repo has no `testpaths` and no `norecursedirs` today (`pyproject.toml:86-95`); after C.3, a bare `pytest` from repo root will collect `legacy/tests/opencode/**` (12 `test_*.py` + conftest) → `ModuleNotFoundError` collection errors (`daemon.opencode` deleted in C.2). Mission commands are explicit-path (`pytest tests/`) so this is not mission-blocking, but it is a guaranteed post-mission dev-workflow landmine. Mitigation: R3 mechanism `testpaths = ["tests"]` (NOT `norecursedirs` — the latter REPLACES pytest's default ignore set, footgun; `testpaths` is one additive line and also stops today's accidental bare-pytest collection of `test/packs/*.py`).
- **Risk #14 (cross-ref, R8 / Phase E) — Phase E proof invalidated by inner_soul runtime writes to base `memory.md`.** Out of Phase C scope. `daemon/tools/inner_soul.py:1043, :1107-1176` writes to `agents/<agent>/memory.md` at runtime; any boot that instantiates a base agent mutates it, breaking the base==v2 `diff -r` equality proof. Mitigation: Phase E captures the diff + manifests BEFORE any boot (R8 capture-before-boot). Phase C contributes the boot-proof transcript at C.5; Phase E sequences the captures.
- **Risk #15 (cross-ref, R9 / Phase B + E) — pin-list misattribution → harmful edits (e.g., `tests/conftest.py:443` HealthResponse fixture) or nonexistent-line chases.** Out of Phase C scope. The real base-agent pin set is concentrated in `tests/test_innate_skills_refactoring.py` (R9-corrected sites: `:59-65, :227, :271, :299`). Phase C's R1/R2/P1 red-window management (R6 invariants) partially overlaps but does not subsume the pin-list risk. Phase B and E own the negative-check for HealthResponse/agents-api fixtures.
- **Risk #16 (architect, R4) — C.2 prose contradiction misleads executor.** Prior C.2 prose claimed grant-map/Makefile edits were "held back from C.1" while C.2's actual edit list is `git rm`-only — executor confusion or accidental split commits. Mitigation: C.2 prose rewritten in this fold pass to be self-consistent; Step 1.5 rationale re-pointed from "grant-map" to "prompt-dir removal"; §6.4 "ALL THREE" corrected to "ALL FOUR" with site enumeration.
- **Risk #17 (architect, R7) — stale pair list false-STOP at gate.** Prior drafts included `agents/leader[v2]` in the migration set; `agents/leader[v2]` does NOT exist on disk (verified). Mitigation: pair set hard-coded to the five (`developer / reviewer / tidier / approver / planner`); the leader agent is explicitly NOT in the migration set; every `leader` reference in the migration-set context in this plan is a stale hit (cleaned in this fold pass).
- **Risk #18 (architect, P6) — C-revert non-atomicity, partial revert ImportError.** Reverting C.2 alone leaves `daemon/manager.py:78` ImportError (the module is gone, the import statement references it); reverting C.1 alone leaves import-dead-code (wiring points at removed module). Mitigation: §7 Phase Rollback documents whole-C-only revert unit; reverting any subset is not safe.
- **Risk #19 (cross-ref, R10 / Phase D + E Step 13) — R10 residue: active-tree opencode reference survives as "note" → gate failure.** Out of Phase C scope. The R10 mandatory-fix set (R10 binding user directive 2026-10-13) upgrades REPORT → FIX semantics for all agent surfaces; tester grep-verifies as a GATE per `phaseE-verification-evidence.md` Step 13 with the expanded exclusion set (W4 update). Phase C contributes the local C.4 gate; Phase D + E own the final R10 binding gate.

(S2: pre-existing risks DROPPED from §13.9 because they are not in the canonical plan-overview register: worktree `dev.sh` invocation drift, parallel-worker branch races, stray repo-root `.db` artifacts, markers-filter narrowing risk, verdict evidence drift. The relevant ones are now cross-referenced under #14, #15, #19 above where they have a plan-overview entry, or absorbed by #1 / #10.)

### 13.10 Acceptance criteria (unchanged in spirit but branch-aware)

Under BRANCH 1 (selected): the full stack is removed; ensemble's own system boots clean; test suite passes; grep sweep clean modulo EXPECTED_LEAVE.

Under BRANCH 2 (retained machinery, NOT exercised in this mission): the flagged exception module stays in `daemon/opencode/`; agent that depends on it must still receive its expected behavior (run that agent once pre-C.4 and post-C.4 in the fenced dev lane to verify parity); the user decision item is filed.

For this mission (VERDICT 2 = BRANCH 1): all of §9 Definition-of-Done criteria are checked against the post-C.5 state. The boot proof must additionally include a "no agent depends on opencode" assertion — verified by `grep -rn 'opencode' agents/ --include=meta.json` returning ZERO matches post-Base.

### 13.11 Open Items (FINAL — post-addendum)

| # | Item | Status | Resolution path |
|---|---|---|---|
| 1 | Pending leader exploration addendum | **RESOLVED** | section §13.1 + §13.2 (verdicts 1 + 2) |
| 2 | Pending branch-point verdict | **RESOLVED → BRANCH 1 by evidence** | §13.2 evidence (a)–(f) + §13.3 branch selection; boundary-rule machinery retained for future |
| 3 | Pending precise classification (comm-service vs session/API coding-worker) | **RESOLVED → one package, two named roles** | §13.1; no split-removal needed |
| 4 | Pending scope additions (MCP configs + InstanceManager OpenCode resources + agent-facing surfaces) | **RESOLVED → no MCP config work exists; InstanceManager + agent-facing already in TIER-1/TIER-2** | §13.2(d) recon proof + §13.4/§13.5 inclusion |
| 5 | `.agents/shared/planning/opencode-native-tools/` inclusion in MOVE SET | **RESOLVED** | §13.4 MOVE SET (7 docs) |
| 6 | `pyproject.toml:88` + `.env.example:39` in TOUCH SET | **RESOLVED** | §13.5 TOUCH SET |
| 7 | task/repository.py:1777 rephrase (was OOS, now TOUCH) | **RESOLVED** | §13.5 TOUCH SET + §13.7 C.4 commit |
| 8 | `example/SKILL.md` archive decision (was DECISION GATE) | **RESOLVED → archive to legacy/** | §13.4 MOVE SET + §13.7 C.3 commit (Go-binary contract spec — historical reference only) |
| 9 | `tests/test_innate_skills_refactoring.py` + `tests/migration/test_jsonb_migration.py` move-vs-edit decision | **RESOLVED → in-place edit (was whole-file move in earlier draft)** | §13.5 TOUCH SET + §13.7 C.4 commit |
| 10 | ORDERING CONSTRAINT (engineering-critical) | **RESOLVED → Step 1.5 BLOCKING GATE before C.2** | §13.7 commit sequence |

**No "pending addendum" markers remain in this plan.** The plan finalizes with this section incorporated.

## 14. Architect Fold Pass (R1–R7 + P1–P3/P6/P8 + R10 binding supersession; 2026-10-13)

This section records the second-pass fold: the architect's `architecture-recommendation.md` validation found the plan SOUND with 9 REQUIRED amendments + 8 RECOMMENDED + 1 R10 binding gate. All have been folded in place into §3/§6/§7/§8/§13 of this document. The architect verified: zero binding decisions challenged, zero USER DECISION ITEMS, v2 load-bearing check PASSED, no structural flaw. Verification is read-only at this point; executor re-greps every load-bearing cite at execution time per project convention.

### 14.1 Folded amendments (R1–R7 — REQUIRED)

| # | Amendment | Folded into | Re-grep at exec time |
|---|---|---|---|
| **R1** | Add `tests/test_tool_filter.py` to §13.5 TOUCH set (3 tests at `:710-716, :750-764, :827-860` go red at C.1; rephrase to a surviving skill in C.4) | §13.5 TOUCH set + Risk #11 | `grep -n opencode tests/test_tool_filter.py` |
| **R2** | Expand `tests/migration/test_jsonb_migration.py` edit spec to full-file surface (`:68-69, :75, :106-107, :194-197, :223-226, :366-367, :497, :518` — not just `:70-107`) | §13.5 TOUCH set + §13.7 C.4 + Risk #12 | `grep -n opencode tests/migration/test_jsonb_migration.py` |
| **R3** | Add `testpaths = ["tests"]` to `pyproject.toml [tool.pytest.ini_options]` (mechanism `testpaths`, NOT `norecursedirs`) | §13.5 TOUCH set + §13.7 C.4 + Risk #13 | `grep -nE 'testpaths\|norecursedirs' pyproject.toml` |
| **R4** | Resolve C.1/C.2 prose contradiction in favor of edit lists; re-point Step 1.5 rationale from "grant-map" to "prompt-dir removal"; fix §6.4 "ALL THREE" → "ALL FOUR"; correct `daemon/services/inheritance/loader.py:226` cite → `daemon/loader.py` (R4) | §6.4 (4 sites), §13.7 C.2 prose rewrite, §13.7 cite line 698, Risk #16 | `grep -n 'inheritance/loader' daemon/ -r` returns 0 |
| **R5** | Makefile edit MUST include the comment (`:173-174` rephrase + `:186` FILES); dropping only FILES leaves comment → own gate FAILS | §13.5 TOUCH set + §13.7 C.4 | `grep -n opencode Makefile` post-C.4 → 0 |
| **R6** | Move C.3's zero-grep gate to C.4; declare per-commit invariants: C.1 = boot-green only, C.2 = boot-green only, C.3 = move-only, C.4 = first test-green | §13.7 commit sequence + per-commit invariants table | `python -m pytest tests/ --timeout=120` post-C.4 |
| **R7** | Fix Phase-B pair list: `agents/leader[v2]` does NOT exist; leader NOT in migration set; de-ambiguate Step 0/Step 1.5 to the five pairs `developer / reviewer / tidier / approver / planner` | §6.3, §13.7 Step 0, Risk #5, §13.6 LEAVE ALONE rationale, §13.7 list-§ (R7 reminder), §13.11 mention, Phase B dependency row | `ls agents/leader[v2]` returns "No such file or directory" |

### 14.2 Folded recommendations (P1–P3, P6, P8)

| # | Recommendation | Folded into | Re-grep at exec time |
|---|---|---|---|
| **P1** | Enumerate all **16 P1 opencode sites** in `tests/test_innate_skills_refactoring.py` (`:11, :14, :56, :59-65, :84-85, :94, :98, :227, :232-236, :241, :271, :275, :280, :287-289, :297-301, :353`) + 3 R1 + 8 R2 = **27 cite locations total across R1+R2+P1**; accepted RED WINDOW B→C.4 | §13.5 TOUCH set row + §13.7 C.4 | `grep -n opencode tests/test_innate_skills_refactoring.py` |
| **P2** | `tests/unit/test_wanderer_agent.py:41-44` mirror edit (same as coder's `:40-43`) | §13.5 TOUCH set | `grep -n external_opencode tests/unit/test_wanderer_agent.py` |
| **P3** | `pyproject.toml:88`: REPHRASE the integration-marker description (do NOT delete the registration row; 5+ files use `@pytest.mark.integration`) | §13.5 TOUCH set + §13.7 C.4 (supersedes prior "drop the marker" framing) | `grep -nE '@pytest.mark.integration' tests/ -r` |
| **P6** | Rollback doc: state Phase C rolls back as a UNIT (C.1↔C.2 mutually broken in partial states) | §7 Phase Rollback + Risk #18 | N/A (rollback is git-level) |
| **P8** | `daemon/tools/instance.py` factory block spans `:5815-5819` (P8 — `tools.extend` at `:5819`) | §13.5 TOUCH set + §6.4 site enumeration | `sed -n '5815,5819p' daemon/tools/instance.py` |

### 14.3 R10 binding supersession (architect §11; 2026-10-13 user directive)

R10 (architect §11) is the binding user directive: post-migration, ANY active-agent-surface opencode reference (definition, prompt, tool config, skill, workflow doc, any agent-facing surface) is a DEFECT → FIX, not a note. Tester grep-verifies as a GATE. Phase E's "what still knows about legacy paths" sweep upgrades REPORT → FIX for all agent surfaces. Only acceptable survivors: (a) the legacy archive itself, (b) historical/audit material.

**Folded supersession:** §13.5's "comment-rephrases" row was previously marked "optional / if unsure LEAVE" — R10 SUPERSEDES that framing. The following is now a MANDATORY-fix set in C.4:

- **Daemon comment/docstring/log-string rephrases (R10):** `daemon/config.py:168`, `daemon/services/keyword_extraction.py` (docstring + log lines; module STAYS per D5), `daemon/services/context_injection.py:541,846`, `daemon/services/context_messages.py:35,39`, `daemon/services/critical_note_gate.py:8`, `daemon/services/waiting_children_watchdog.py:9,752`, `daemon/loader.py:226`, `daemon/repositories/task/repository.py:1777` (D5 "may not churn" hesitation SUPERSEDED for this comment-only cite; code untouched, rephrase mandatory).
- **Former-P7 cosmetic stragglers (R10):** `daemon/tools/instance.py:5823, :5860, :6246` trailing comments + grant-map header `:155-156`; `tests/job_queue/conftest.py:65`; `tests/unit/services/test_critical_notes_phase2_filters.py:11`; `tests/integration/test_auth_boot_preflight.py:20`; `test/packs/lcan_helpers/mock_llm.py:141-142`; `test/packs/regression_integration_test.sh:31, :40`; `test/packs/regression_chat_source_integration_test.sh:8`.
- **Manifest wording:** `Makefile:173-174` comment + `:186` FILES (R5); `pyproject.toml:88` integration marker (P3); `.env.example:39` (already in TOUCH set).
- **Phase D docs:** `docs/agents.md`, `docs/skill-evolution.md:174`, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/test-suite-known-issues.md`, `docs/plans/*`, `docs/optimization/*`, `docs/mcp-integration.md`, `agents/{developer,reviewer,tidier,approver,planner}/**` opencode-comment-in-prose mentions (Phase D owns).

**Final whole-tree gate (R10, architect §11.3):** Phase E's tester gate is `git grep -niE 'opencode' -- .` with the allowlist exclusions listed in §13.8. The tester produces an allowlist file (one row per excluded path + one-sentence justification; rows without justification fail the gate review). Phase C's local C.4 gate is the local pre-handoff check; Phase E's R10 gate is the binding final check.

**v2 load-bearing check (R10 §11.5):** verified clean in the worktree — `grep -rniE 'opencode'` over all five v2 trees returns ZERO matches. The R10 gate is satisfiable WITHOUT violating v2 zero-touch (D4). No user decision item.

### 14.4 Architect risk rows folded into §13.9 (Risks #11–#18)

| # | Risk | Source | Folded into |
|---|---|---|---|
| 11 | `tests/test_tool_filter.py` 3 tests red at C.1 | R1 | §13.9 Risk #11 |
| 12 | `test_jsonb_migration.py` collection ImportError post-C.2; opencode surface beyond `:70-107` | R2 + R6 | §13.9 Risk #12 |
| 13 | Bare `pytest` post-C.3 collects `legacy/tests/**` → ImportError storm | R3 | §13.9 Risk #13 |
| 14 | Phase E proof invalidated by inner_soul runtime writes to base `memory.md` | R8 (Phase E governs, not C; noted for cross-reference) | Phase E plan (out of scope here) |
| 15 | phaseB pin-list misattribution | R9 (Phase B governs) | Phase B plan (out of scope here) |
| 16 | C.2 prose contradiction misleads executor | R4 | §13.9 Risk #16 |
| 17 | Stale §5 Step 0 pair list (`leader[v2]` absent) → false STOP at gate | R7 | §13.9 Risk #17 |
| 18 | C-revert non-atomicity: partial Phase-C revert → `manager.py:78` ImportError | P6 | §13.9 Risk #18 + §7 Phase Rollback (whole-C unit) |

(Risks #14 and #15 are Phase B/E governance items noted in the architect §5 risk register; they do NOT affect Phase C's edit surface — cross-referenced only.)

### 14.5 Architect validation summary (binding, for the dispatcher)

| Property | Verdict | Citation |
|---|---|---|
| Plan structure | SOUND — no binding decision challenged | architect §1 |
| v2 zero-touch | INTACT — D4 binding preserved | architect §1, §3.5, §11.5 |
| Migrations append-only | INTACT — zero `daemon/migrations/` opencode hits | architect §3.2 |
| `legacy/` scope isolation | INTACT — no `legacy/` leaks into release/discovery/integrity paths | architect §3.3 |
| Frozen-binary neutrality | INTACT — KNOWN_TOOL_NAMES excision safe in all 3 planes | architect §3.1 |
| Git-only reversibility | INTACT — every removal is `git rm`/`git mv`; legacy/ additive; runtime DBs untouched (D7) | architect §7 |
| No-promote (D8) | INTACT — no new migration file; CHANGELOG edit only; no promote-machine trigger | architect §3.4 |
| Branch-point verdict | BRANCH 1 by evidence; boundary-rule machinery retained | architect §8 (zero user decision items) |
| R10 active-tree gate | SATISFIABLE without violating v2 zero-touch | architect §11.5 |
| **USER DECISION ITEMS** | **NONE** | architect §8 |

### 14.6 Open items (post-fold, post-R10)

| # | Item | Status |
|---|---|---|
| 1 | Pending leader exploration addendum | RESOLVED (§13.1 + §13.2) |
| 2 | Pending branch-point verdict | RESOLVED → BRANCH 1 by evidence |
| 3 | Pending precise classification | RESOLVED → one package, two named roles |
| 4 | Pending scope additions | RESOLVED → no MCP work exists |
| 5 | `.agents/shared/planning/opencode-native-tools/` MOVE | RESOLVED (§13.4) |
| 6 | `pyproject.toml:88` + `.env.example:39` TOUCH | RESOLVED + R3 (testpaths), P3 (rephrase) folded |
| 7 | `task/repository.py:1777` rephrase | RESOLVED + R10 (mandatory) folded |
| 8 | `example/SKILL.md` archive | RESOLVED → legacy/ |
| 9 | test files move-vs-edit | RESOLVED → in-place edit (C.4) |
| 10 | ORDERING CONSTRAINT | RESOLVED → Step 1.5 BLOCKING GATE |
| 11 | R1 (test_tool_filter) | RESOLVED → §13.5 TOUCH + Risk #11 + C.4 |
| 12 | R2 (test_jsonb_migration full-file) | RESOLVED → §13.5 TOUCH + Risk #12 + C.4 |
| 13 | R3 (pyproject testpaths) | RESOLVED → §13.5 TOUCH + Risk #13 + C.4 |
| 14 | R4 (prose contradiction + cite fixes) | RESOLVED → §6.4 (4 sites), §13.7 C.2 prose, §13.7 cite, Risk #16 |
| 15 | R5 (Makefile comment) | RESOLVED → §13.5 TOUCH + C.4 |
| 16 | R6 (per-commit invariants) | RESOLVED → §13.7 commit sequence + table |
| 17 | R7 (pair list) | RESOLVED → §6.3, §13.7 Step 0, §13.6, Risk #17 |
| 18 | P1 (test_innate_skills sites) | RESOLVED → §13.5 TOUCH + C.4 |
| 19 | P2 (wanderer_agent mirror) | RESOLVED → §13.5 TOUCH + C.4 |
| 20 | P3 (pyproject rephrase) | RESOLVED → §13.5 TOUCH + C.4 |
| 21 | P6 (whole-C rollback) | RESOLVED → §7 + Risk #18 |
| 22 | P8 (instance.py :5819) | RESOLVED → §13.5 TOUCH + §6.4 |
| 23 | R10 (mandatory comment rephrases + Phase E gate cross-ref) | RESOLVED → §13.5 TOUCH (mandatory) + §13.8 cross-ref to Phase E R10 gate |
| 24 | R8 (inner_soul memory.md capture-before-boot) | DEFERRED to Phase E plan (out of C scope) |
| 25 | R9 (phaseB pin-list misattribution) | DEFERRED to Phase B plan (out of C scope) |
| 26 | P3 sub-bullet: 5+ files use `@pytest.mark.integration` | RESOLVED → P3 framing preserved (rephrase not delete) |
| 27 | **C-1 (fix pass, CRITICAL)** — phaseC move-vs-edit contradiction (6 stale sites: §9 DoD #13, §5 Step 3, §3 Phase A row, §2, §4.2, §10 EXPECTED_LEAVE) | **RESOLVED** — §9 DoD #13 rewritten to "edited in place per §13.5, zero opencode references post-C.4"; §5 Step 3 two `git mv` lines for the shared test files deleted + commit-message rows scrubbed; §3 Phase A row corrected (Phase A's move-set is STACK-1..11, NEVER produces `legacy/tests/...` shared-test files); §2 / §4.2 / §10 EXPECTED_LEAVE scrubbed; "superseded-by-§13" notes added at the head of §5 and §9 |
| 28 | **W4 (fix pass, phaseC site)** — R10 gate cross-ref in §13.8 had non-reachable exclusion set; expand + allowlist rows | **RESOLVED** — §13.8 R10 cross-ref updated to the EXPANDED exclusion set (`.agents/shared/planning/**`, `.agents/*/RESULTS*`, `.agents/*/LESSONS*`, `.agents/tester/PACKS.md`, `.kilo`); per-row allowlist justification table added; §13.8 explicitly cross-references phaseE Step 13 as the single source of truth for the verbatim command |
| 29 | **S2 (fix pass)** — risk #13 defined twice with divergent content in §13.9; align #14/#15 with plan-overview risk register | **RESOLVED** — §13.9 rewritten to mirror plan-overview rows 11-19 (architect-attributed); pre-existing Risk #13 (verdict evidence drift) DEDUPED (covered by plan-overview #10); pre-existing #11, #12, #14, #15 local risks (worktree dev.sh, branch races, stray .db, markers-filter) DROPPED from §13.9 (not in canonical register); #14 and #15 added as cross-references to plan-overview #14 (R8/Phase E inner_soul) and #15 (R9/Phase B/E pin-list) |
| 30 | **S4 (fix pass)** — C.1 boot proof missing pre-flight check for ensemble_dev DB reachability | **RESOLVED** — added S4 pre-flight block in the C.1 boot proof section: confirm `ensemble_dev` DB is reachable (worktree-local data pins; ENSEMBLE_SELF_ENV=dev; PORT=8079; ENSEMBLE_POSTGRES_DB=ensemble_dev) BEFORE the boot attempt; recommended probe commands (psql / SQLite file check / curl health endpoint) |
| 31 | **S5 (fix pass)** — §13.5 "17+ sites" phrasing doesn't match the literal enumeration | **RESOLVED** — replaced "17+ sites" with explicit count **"27 cite locations across R1 + R2 + P1 (3 R1 + 8 R2 + 16 P1)"** in §13.5 + §13.7 C.4 + §14.4 fold-pass P1 row. The number 27 matches the actual enumeration (16 P1 sites + 3 R1 sites + 8 R2 sites); the executor's grep at exec time is the final arbiter. |
| 32 | **N2 (fix pass 2)** — §5 preamble "superseded-by-§13" note must EXPLICITLY cover the C.1–C.5 commit-split, not only the test-file move-vs-edit decision | **RESOLVED** — §5 preamble updated to a 4-bullet authoritative-source note: (1) **C.1–C.5 commit split** (executor MUST follow §13.7's sequence; older §5 Steps 1-4 NOT authoritative); (2) **Per-commit invariants (R6)** — C.4 = FIRST test-green commit; (3) **Test-file move-vs-edit decision**; (4) **Commit-message templates** use §13.7 `[agent-base-v2/C/N]` prefixes. Closing line: "When in doubt, follow §13." |
| 33 | **N5 (fix pass 2)** — Makefile cite drift beyond `:173-174/:186` | **RESOLVED** — all 5 Makefile cites within canonical set: §2 (line 74) and §4.4 (line 155) harmonized to `:173-174, :186` (R5 framing); §13.5 (line 825) and §14.4 (line 980) already used the full range; line 432 (commit-message text) and line 414 (verification grep) are generic. Zero cites beyond `:186`. |
| 34 | **B3 (fix pass 3, BLOCKING)** — census gap: `agents/maintenancer/knowledge/01-architecture-overview.md` has live opencode refs at `:6` (`MCP/OpenCode` mention in "Facade + registry") and `:16` (`OpenCode keeps its own SQLite` in "Checkpoint + CLE"); Phase C TOUCH set covered only explorer/devops souls + `_prompt_system/knowledge.md`; agent-facing surface → MANDATORY fix per R10 (NOT allowlist-eligible) | **RESOLVED** — file added to §13.5 "Cosmetic one-liners" TOUCH row (line 707) with the two opencode lines explicitly enumerated (`:6` and `:16`) so the executor edits precisely. File has 22 lines total; only these 2 carry the opencode mention. Behavior-neutral edit; no test relies on these strings; verified read-only via `cat agents/maintenancer/knowledge/01-architecture-overview.md`. Also cross-referenced in §13.8 "NOT in the exclusion list" callout (line 898) — fixed, not allowlist-eligible. |
| 35 | **B1 (fix pass 3, gate-command mirror part)** — phaseE Step 13's R10 gate exclude set is being aligned by the sibling worker; mirror the SAME aligned set in §13.8 cross-reference with the COMPLETE inline list | **RESOLVED** — §13.8 cross-ref now mirrors the COMPLETE aligned exclusion set inline: base set (`legacy`, `CHANGELOG.md`, `docs/incidents(+/**)`, `.agents/shared/planning(+/**)`, `.agents/*/RESULTS*`, `.agents/*/LESSONS*`, `.agents/tester/PACKS.md`, `.kilo`, `backup(+/**)`, `plugins/opendesign/copy_freely/design-systems/opencode-ai/**`, `docs/mcp-kb-usage.md`, `docs/bugs(+/**)`) + additions (`.agents/*/memories(+/**)`, `.agents/*/working(+/**)`, `.agents/*/EVIDENCE*`, `.agents/approver/active.md`, `.agents/approver/agent-base-v2-tracking.md`, `.slim(+/**)`, `LESSONS(+/**)`, `docs/plans(+/**)`, `docs/test-suite-known-issues.md`, `plugins/opendesign/copy_freely/HASHES.sha256`); both bare-path and `/**` suffix variants for each directory; per-row allowlist table with one-sentence justification per row. **NOT in the exclusion list (FIXED, not allowlist):** `agents/maintenancer/knowledge/**` (B3) and `docs/mcp-integration.md` (sibling's B2). §13.8 remains a cross-reference to phaseE Step 13 (single source of truth for the verbatim command). |
| 36 | **Convergence addendum (fix pass 3, planner-independent-gate execution)** — Step 13 gate at HEAD leaked 16 test files that were in NO plan set. Phase C side: classify each file read-only (what does each opencode mention DO?) and add the TOUCH-class ones to §13.5 C.4. Classification rule: (a) import/mock of opencode symbols → C.4 TOUCH rephrase; (b) NEGATIVE GUARD (asserts opencode ABSENCE — string is the assertion) → NOT mine; sibling allowlists (cross-reference only); (c) stale comment/prose in test code → C.4 TOUCH cosmetic. | **RESOLVED** — added §13.5 "Mock-strip family (extended, convergence addendum)" row with the full 16-file classification breakdown: **class (a) TOUCH C.4 rephrase — 11 files / 16 patch sites**: `test/packs/wc_wake_off_bytecompat_probe_test.py:293`; `tests/test_council_tools.py:55`; `tests/test_governor_integration.py:61, :955`; `tests/test_governor_recursion_acceptance_walk.py:336`; `tests/test_spawn_team_members.py:58`; `tests/tools/test_auto_kill_integration.py:992`; `tests/tools/test_send_message_context_param.py:106`; `tests/tools/test_send_message_load_skill.py:77`; `tests/unit/test_governor_recursion_guard.py:170`; `tests/unit/tools/test_spawn_councilor_default_version.py:126`; `tests/unit/tools/test_version_tag_tool_resolution.py:432, :1104, :1292`. **class (b) NEGATIVE GUARD → cross-reference (sibling allowlists) — 4 files**: `tests/test_spawn_instance_instructive_errors.py:26, :42, :50, :362, :368` (skipped test fixtures — strings dead in `@pytest.mark.skip` test bodies per existing plan §2 line 47); `tests/unit/test_devops_agent.py` (multiple `test_no_opencode_*` and `test_devops_does_not_have_opencode_tools` negative guards — passes even stronger post-C.2); `tests/unit/test_reviewer_v2_agent.py:130-135` (`test_opencode_not_in_innate_skills` D7 reviewer[v2] guard — passes after base==v2); `tests/unit/tools/test_prompt_section_reference_integrity.py:815` (integrity test's own self-test fixture — `LEGIT_SEE_SHAPE_FIXTURES` tuple; opencode mention is a sample string inside the tuple, not a daemon/code reference). **class (c) TOUCH C.4 cosmetic (5 docstring sites)**: `tests/integration/test_persistence_w1_markers.py:15`; `tests/test_governor_recursion_acceptance_walk.py:35`; `tests/test_spawn_team_members.py:42`; `tests/tools/test_send_message_context_param.py:84`; `tests/tools/test_send_message_load_skill.py:55`. **C.4 red-window / invariant text** extended to enumerate the expanded 11-file mock-strip + 5-file cosmetic list, and the 4 negative-guard files explicitly excluded (sibling's territory). C.1 per-commit-invariants table row for C.1 cross-references the convergence addendum. |
| 37 | **Reconciliation flip (convergence addendum post-execution)** — planner gate re-execution found 2 of the convergence addendum's class-(b) "sibling allowlists" files were NOT allowlist-excluded by the sibling (its decomposition lists them as TOUCH) → flip BOTH to TOUCH. (a) `tests/test_spawn_instance_instructive_errors.py:26, :42, :50, :362, :368` — plain editable strings inside `@pytest.mark.skip` test bodies (dead code either way); rephrase the agent-id string to a still-existing agent id (e.g. `"coder"`/`"developer"`/`"designer"`/`"watcher"`); behavior-neutral, tests stay skipped. (c) `tests/unit/tools/test_prompt_section_reference_integrity.py:815` — `LEGIT_SEE_SHAPE_FIXTURES` sample string; rephrase to a non-opencode sample (e.g. "2-3 background jobs"); behavior-neutral. **Do NOT exclude `test_prompt_section_reference_integrity.py` from anything** — it IS the integrity sweep itself; excluding it would mask future leaks in it. | **RESOLVED** — §13.5 "Mock-strip family (extended)" row updated: `tests/test_spawn_instance_instructive_errors.py:26, :42, :50, :362, :368` moved from class (b) to **class (a) TOUCH C.4 rephrase** (mock_meta.id = "opencode", validate_agent_id("opencode"), manager.spawn_instance("opencode") etc. — rephrase the agent-id string to a still-existing agent id; tests stay skipped); `tests/unit/tools/test_prompt_section_reference_integrity.py:815` moved from class (b) to **class (c) TOUCH C.4 cosmetic** (the `LEGIT_SEE_SHAPE_FIXTURES` parenthetical sample rephrased to a non-opencode string). Final tally: 12 files class (a) / 21 patch sites / 6 files class (c) / 6 docstring sites / 2 files class (b) cross-reference. C.4 R6 invariant enumeration (line 817) updated: added `tests/test_spawn_instance_instructive_errors.py:26,:42,:50,:362,:368` to the mock-strip list (class a), `tests/unit/tools/test_prompt_section_reference_integrity.py:815` to the cosmetic-docstring list (class c); the 2 remaining class (b) negative-guard files (`tests/unit/test_devops_agent.py`, `tests/unit/test_reviewer_v2_agent.py:130-135`) are NOT in C.4 edit surface. |

**No open items remain in Phase C's scope.** Items 24-25 are Phase B/E governance items — cross-referenced but not in Phase C's edit surface. The plan finalizes with this fold pass + fix passes 1-3 + convergence addendum incorporated.
