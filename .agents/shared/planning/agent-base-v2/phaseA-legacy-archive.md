# Phase A: Legacy Archive (opencode-era)

## Objective

Move all opencode-era artifacts out of the live tree (5 base agent dirs whose v2 counterparts replace them + the ENTIRE opencode stack — one retirement unit with two roles: protocol/comm dispatch in `daemon/opencode/server.py` + session/API lifecycle across the other 7 stack modules, plus the sole comm-consumer shim `daemon/tools/external_opencode.py`, the agent-facing innate-skill surface, the test pack layer, the planning history, the bug docs, and the ported Go-binary contract spec) into a freshly-created root-level `legacy/` directory that mirrors the source shape, before any Phase B content replacement. **The archive is the source-of-truth safety net for this mission**: any later divergence, surprise, or rollback request can be answered from on-disk bytes. The classification and live-dependency verdict are FINAL — exploration addendum incorporated; no pending items remain.

## Scope

### In Scope

1. Create `legacy/` at the repo root (gitignored-equivalent — see D2 justification: `backup/` precedent at repo root is git-tracked for similar archival purpose, so `legacy/` is git-tracked too unless v0.20.x `.gitignore` excludes it — executor must `grep -nE '^\s*legacy/?\s*$' .gitignore` and RECORD the result before commit; if absent, this phase's PR adds a tracking decision noted in DoD).
2. Capture and archive the **complete pre-overwrite content** of these 5 base agent dirs:
   - `agents/approver/`
   - `agents/developer/`
   - `agents/planner/`
   - `agents/reviewer/`
   - `agents/tidier/`
3. Archive the **opencode service move-set** as a verbatim byte-copy of the listed paths under `legacy/` mirroring source shape.

### Out of Scope (handled by sibling phases)

- **Tier-1 wiring removal + test move/strip** (Phase C `phaseC-opencode-excision.md`) — Phase A only MIRRORS the files; it does NOT delete them from the live tree. The live tree is left intact during Phase A — D2 sequencing mandates archive BEFORE overwrite.
- **Documentation references + bug doc updates** (Phase D `phaseD-references-docs.md`).
- **Whole-mission verification evidence pack** (Phase E `phaseE-verification-evidence.md`) — Phase A's manifests feed Phase E but the verification pack is built there.
- **Phase B base replacement** (this directory `phaseB-base-copy.md`).
- **No live system contact** (binding D8 mission rule): no promote, no daemon restart, no DB writes.
- **No edits under `agents/*[v2]/`** (binding D4 mission rule): only archive the opencode-era BASE content; v2 is not touched in any way.

### Justification for scope boundary

- The dual mandate ("archive opencode-era base + comm service to legacy source dir") puts ALL base-overwrite prep in Phase A. Wiring excision (Phase C) and references update (Phase D) naturally come AFTER both archive (Phase A) and copy (Phase B) because those phases *use* the new base dirs.
- `legacy/` mirroring source shape (rather than one flat tarball) preserves git/grep-ability without re-bundling, which is the same precedent `backup/` follows.

## Decision Points (resolved in this plan, justifications cited)

| # | Decision | Choice | Justification |
|---|----------|--------|---------------|
| **D-A1** | Planning-doc placement: 7 opencode-native-tools planning files under `legacy/.agents/shared/planning/opencode-native-tools/` vs `.agents/shared/planning/archive/opencode-native-tools/` | **legacy/ mirror (FINAL — addendum-resolved)** | User binding D2 says default = legacy/ mirror; the exploration addendum confirms and locks this placement. The 7 docs are HISTORICAL PLANNING DOCUMENTS for the opencode replacement; co-locating them with the archived code they describe preserves provenance. The planning/archive/ convention used by `job-as-front-primitive/` was considered and REJECTED for this stack (addendum verdict). Decision is FINAL. |
| **D-A2** | `example/SKILL.md` inclusion | **INCLUDED (FINAL — addendum-resolved)** | Addendum verdict: `example/SKILL.md` is the ported Go binary's contract spec (`name: opencode-skill`, `metadata: {version: "1.2.0", author: Kha Nguyen, license: MIT, ...}`); it documents the opencode_skill API surface and is IN-SCOPE for the legacy archive. The Step 2 preflight `grep -i opencode example/SKILL.md` resolves `ARCHIVE_EXAMPLE_SKILL=1`. Archive target: `legacy/example/SKILL.md`. |
| **D-A3** | Hash tool | **`shasum -a 256` exclusively** | Matches `scripts/upgrade/lib.sh:1928` `_tree_manifest` and `:1944` `_tree_hash_of_lines` line format `<sha>  <relpath>`; reuse the recipe so Phase E evidence compares apples-to-apples with release tooling. |
| **D-A4** | Archive transport | **`cp -pR <src>/. <dst>/` per source path** | Preservation of file permissions + mtime + symlinks is required by D3 (byte-identical to source); `cp -pR` is the safest standard idiom and matches `stage.sh:397`'s payload-copy family. `tar`/`rsync` would introduce different permission edge cases. |
| **D-A5** | Preflight ordering | **manifest snapshot of source paths (pre-archive) + manifest snapshot of legacy-build-dest (post-archive) → diff → archive** | Two snapshots before-and-after. v2 captured before Phase B (D4 ZERO-TOUCH proof); legacy-build captured after the archive copy in Phase A. |
| **D-A6** | Stack classification precision | **FINAL — addendum-resolved, no longer pending** | The exploration addendum established authoritative classification: (i) `daemon/opencode/` is ONE retirement unit with TWO named roles (comm dispatch in `server.py` + session/API lifecycle in `client.py`/`session_manager.py`/`state.py`/`registry.py`/`repository.py`/`__init__.py`/`constants.py`); (ii) `daemon/tools/external_opencode.py` is the sole comm-consumer of `server.py` action dispatch; (iii) the `__init__.py:13` "used by the ensemble HTTP router" claim is STALE — zero router mounts verified (30 routers + `api.py` clean). Plan structure is FINAL; the executor does NOT extend the move-set. Any unexpected opencode-tied file surfaced at execution time is an EXECUTION ANOMALY that halts Phase A and re-engages the user (NOT silently archived). |
| **D-A7** | Live-dependency verdict | **EMPTY retained-live state (FINAL — addendum-resolved)** | Addendum verdict: NO live dependency on the opencode stack. Coder is opencode-free BY DESIGN (`agents/coder/meta.json` innate_skills = `["todo", "chart", "dynamic-skill"]`, no opencode). "opencode" innate skill declared ONLY in the 5 retiring base agents' meta.json (which Phase B replaces with v2 = no opencode). ZERO meta.json lists `external_opencode` in `tools.allow`. Runtime invocation outside tests is DEAD (manager wiring passive; startup recovery DISABLED per `daemon/manager.py:3325-3338`; no HTTP until a granted tool call, and no granted caller). No MCP configs anywhere. Port 4095 nothing listening. `~/.opencode_skill`, `~/.opencode` absent. `keyword_extraction.py` + `context_injection.py` are LIVE-SHARED infra — they STAY (live callers: `attestation_report_judge.py:35-328`, `graph.py:10794`, `plugin_subsystem/opendesign/generate.py:650`). Schema retained for future-proofing, but `retained-live` rows are EMPTY in this map. |
| **D-A8** | Ordering constraint | **BEFORE or COUPLED WITH Phase C, but NEVER before Phase B** | Engineering-critical (addendum-issued): the stack removal MUST land WITH or AFTER Phase B's base := v2 retirement. Rationale: while the 5 old-base agents still declare `"innate_skills": ["opencode", ...]` (their CURRENT base meta.json state), the innate-skills loader MUST resolve the `opencode` innate skill (its dir at `agents/_prompt_system/innate-skills/opencode/` AND its entry in `daemon/tools/instance.py:162 INNATE_SKILL_TOOL_CATEGORIES`). Removing the stack BEFORE Phase B's meta.json replacement breaks the loader's path resolution. Phase A's pure-copy archive is safe anytime. Phase C's wiring excision MUST execute WITH or AFTER Phase B. |

## OpenCode Stack Scope — Entire Stack (FINAL, addendum-resolved)

> **SCOPE STATEMENT (binding, FINAL — supersedes earlier "two-lane" framing; exploration addendum incorporated):** *"Remove the opencode stack (related modules) in ensemble and backup to legacy."* The unit of work is the **ENTIRE opencode stack**: comm lane + session/API coding-worker lane + shared modules inside `daemon/opencode/` + OpenCode resources the InstanceManager coordinates + MCP configs (none found — verified) + agent-facing surfaces (the innate opencode skill prompt + shared prompt-system mentions + comment references) + the ported Go-binary contract spec. ALL of it is removed from the ensemble's active codebase and backed up to the SAME root-level `legacy/` dir (copy-then-verify, preserved but unused). The exploration addendum is AUTHORITATIVE; this plan reflects it and no longer carries "pending addendum" markers.

### Verdict 1 (addendum-confirmed): ONE retirement unit, TWO named roles

`daemon/opencode/` is a single package — the line-by-line Python port of the `opencode_skill` Go binary (`server.py:168+` ports `server.go:230-507`). The two roles:

- **Protocol/comm role** — the action-dispatch + question-poll/relay layer INSIDE the session lane (`server.py`). Its SOLE consumer is the tool-shim `daemon/tools/external_opencode.py:20-21`.
- **Session/API role** — the lifecycle/transport/state/repo modules (`client.py`, `session_manager.py`, `state.py`, `registry.py`, `repository.py`, `constants.py`, `__init__.py`).

Single import graph: `external_opencode.py` → `server.py` → `registry` / `session_manager` → `client`. Single SQLite store (`opencode_sessions` table, created at engine-factory time per `repository.py`). Single manager init (`daemon/manager.py:1427-1436`). The `daemon/opencode/__init__.py:13` docstring claim "used by the ensemble HTTP router" is **STALE** — zero router mounts verified across 30 routers + `api.py`.

The plan treats the stack as **ONE move** with **two roles named in the map**. `daemon/tools/external_opencode.py` is archived as the sole comm-consumer shim.

### Verdict 2 (addendum-confirmed): NO live dependency → FULL STACK REMOVAL

- `coder` agent is opencode-free **BY DESIGN** (`agents/coder/` zero opencode hits across `meta.json`/`soul.md`/`workflow.md`; `tools.allow` has no `external_opencode` category; `tests/unit/test_coder_agent.py:40-43` is a CONTAMINATION GUARD that fails the test if coder ended up with opencode).
- `"opencode"` innate skill declared ONLY in the 5 retiring base agents' `meta.json` (which Phase B replaces with v2 = no opencode).
- ZERO `meta.json` lists `external_opencode` in `tools.allow` across the entire `agents/` tree (verified 2026-10-13).
- Runtime invocation outside tests is DEAD (manager wiring passive; startup recovery DISABLED per `daemon/manager.py:3325-3338`; no HTTP until a granted tool call, and no granted caller — no active tool surface for opencode).
- NO MCP configs anywhere (`daemon/mcp/` zero opencode refs; built-ins = `context7`/`plane`/`webfetch`; `config.yaml` zero opencode refs).
- Port 4095 nothing listening; `~/.opencode_skill`, `~/.opencode` absent.
- **LIVE-SHARED INFRA THAT STAYS**: `daemon/services/keyword_extraction.py` + `daemon/services/context_injection.py` (and their docstring/comment references in `context_messages.py` + `critical_note_gate.py` + `loader.py:226` + `config.py:168` + `task/repository.py:1777` + `waiting_children_watchdog.py`) are LIVE-SHARED infrastructure with active callers (`attestation_report_judge.py:35-328`, `graph.py:10794`, `plugin_subsystem/opendesign/generate.py:650`). They get **COMMENT REPHRASES** (opencode→ensemble-internal-generic) but **NEVER removed/relocated** — comment edits only.

**Boundary-rule exception (D-A7): EMPTY** — no surface requires `retained-live` status. The terminal-state schema is preserved in the before/after map for future-proofing, but `retained-live` row count = 0.

### Boundary rule (binding) — kept for future-proofing

If a future sweep or addendum identifies an ACTIVE live-used agent with a real opencode-stack dependency:

- The plan DOES NOT break it mid-migration.
- The plan isolates that dependency, keeps the needed module in place (NOT archived).
- The plan flags it prominently with options as a USER decision item (re-point to native system vs retire the agent), never silently resolved.

### Ordering constraint (D-A8) — engineering-critical

While the 5 old-base agents still declare `"innate_skills": ["opencode", "chart", "todo"]` in their CURRENT base `meta.json` (state at worktree HEAD `885a19d4f`), the innate-skills loader MUST resolve:

- the `opencode` innate skill's prompt dir at `agents/_prompt_system/innate-skills/opencode/`, AND
- the `opencode` entry in `daemon/tools/instance.py:162` `INNATE_SKILL_TOOL_CATEGORIES` mapping.

Removing the stack (Phase C wiring excision) **MUST land WITH or AFTER Phase B's base := v2 retirement**, never before. Phase B's copy replaces the 5 base `meta.json` files with v2 content (`["todo", "chart", "dynamic-skill"]`), making the opencode innate-skill loader path unreachable. After that point, removing the stack is safe.

Phase A's pure-copy archive is safe anytime — it does NOT mutate any live tree path. The constraint binds Phase C's execution, not Phase A's archive step. The plan documents this constraint for the executor and downstream Phase C author.

### Three terminal states (before/after map schema)

| Terminal state | Meaning | On-disk | Evidence |
|----------------|---------|---------|----------|
| **archived-to-legacy** | Whole files/dirs removed from live source; mirror shape copied to `legacy/...` | live path removed (Phase C); `legacy/<source-shape>` archive | per-file sha256 diff vs source (must be empty) |
| **edited-in-place** | Files EDITED in live source; pre-edit byte content preserved by **git history** (PRIMARY source of truth) + optional diff artifact in `evidence/phaseA/pre-edit-snapshots/` (SECONDARY mitigation only) | unchanged live path (Phase C edits; pre-edit byte = git HEAD before PR) | git diff; `evidence/phaseA/pre-edit-snapshots/<file>.before.txt` (optional) |
| **retained-live** (boundary-rule exception, USER decision) | Whole file/dir KEPT in active codebase because an active agent depends on it; flagged for user decision | unchanged live path + `evidence/phaseA/boundary-exceptions/<surface>.md` documenting the live-used proof + user-decision options | MANDATORY: `evidence/phaseA/boundary-exceptions/<surface>.md` (live-used proof + user-decision options) |

The plan keeps the schema for all three states. The current map has rows in `archived-to-legacy` and `edited-in-place`; `retained-live` is **EMPTY per Verdict 2** (recorded as `[]` for future-proofing).

---

## BEFORE/AFTER MAP (FINAL — addendum-resolved)

> The final, complete map. **Every row is FINAL** — the exploration addendum resolved the classification. The executor does NOT extend the move-set; any new opencode-tied file surfaced at execution time is an EXECUTION ANOMALY that halts Phase A and re-engages the user.

### Move set (archived-to-legacy) — mirror source shape

| Map ID | Live source (pre-archive) | Archive target (legacy/) | Stream / role | Verification |
|--------|---------------------------|--------------------------|---------------|--------------|
| **BASE-1** … **BASE-5** | `agents/{approver,developer,planner,reviewer,tidier}/` (pre-overwrite content) | `legacy/agents/<name>/<per-dir files>` | base content archive (see per-pair file list in Phase B's `phaseB-base-copy.md` File Inventory) | per-pair `diff -r` + sha256 |
| **STACK-1** | `daemon/opencode/` (8 modules: `__init__.py`, `client.py`, `constants.py`, `registry.py`, `repository.py`, `server.py`, `session_manager.py`, `state.py`) | `legacy/daemon/opencode/` | **single retirement unit, two roles**: protocol/comm (`server.py` action dispatch) + session/API lifecycle (client/session_manager/state/registry/repository) | per-module `diff` + sha256 |
| **STACK-2** | `daemon/tools/external_opencode.py` (file) | `legacy/daemon/tools/external_opencode.py` | sole comm-consumer shim (consumes `daemon.opencode.server.external_opencode_send_message`) | per-file diff + sha256 |
| **STACK-3** | `agents/_prompt_system/innate-skills/opencode/{skill.md, skill.md.bak}` (2 files including dotfile) | `legacy/agents/_prompt_system/innate-skills/opencode/{skill.md, skill.md.bak}` | agent-facing surface — innate skill prompt | per-file diff + sha256 |
| **STACK-4** | `tests/opencode/` (12 `.py` files: `__init__.py`, `conftest.py`, `test_client.py`, `test_error_propagation_edge_cases.py`, `test_integration.py`, `test_registry.py`, `test_repository.py`, `test_server.py`, `test_session_manager.py`, `test_state.py`, `test_table_creation.py`, `test_tools.py`) | `legacy/tests/opencode/` | uniform single tree (executor confirms 12-file count at run time) | dir-tree `diff -r` + sha256 |
| **STACK-5** | `test/packs/opencode_native_tools_unit_test.sh` (file) | `legacy/test/packs/opencode_native_tools_unit_test.sh` | pack wrapper | per-file sha256 |
| **STACK-6** | `test/packs/ab/top_opencode_unit_test.sh` (file) | `legacy/test/packs/ab/top_opencode_unit_test.sh` | AB-dir pack wrapper | per-file sha256 |
| **STACK-7** | `test/packs/regression_opencode_e2e_test.sh` (file) | `legacy/test/packs/regression_opencode_e2e_test.sh` | e2e regression pack | per-file sha256 |
| **STACK-8** | `.agents/shared/planning/opencode-native-tools/` (7 `.md` files: `plan-overview.md`, `go-to-python-parity-check.md`, `phase1-plan.md`, `phase2-plan.md`, `phase3-plan.md`, `phase4-plan.md`, `phase5-plan.md`) | `legacy/.agents/shared/planning/opencode-native-tools/` | mission planning history (D-A1 placement locked) | dir-tree `diff -r` + sha256 |
| **STACK-9** | `docs/bugs/opencode-session-manager-memory-leak.md` (file) | `legacy/docs/bugs/opencode-session-manager-memory-leak.md` | bug doc (opencode-era) | per-file sha256 |
| **STACK-10** | `docs/bugs/external-opencode-wait-latency.md` (file) | `legacy/docs/bugs/external-opencode-wait-latency.md` | bug doc (opencode-era) | per-file sha256 |
| **STACK-11** *(D-A2 FINAL-resolved)* | `example/SKILL.md` (file, 5489 bytes; the ported Go binary's contract spec — `name: opencode-skill`, `metadata: {version: "1.2.0", author: Kha Nguyen, license: MIT, github_url: https://github.com/disillusioners/opencode-skills}`) | `legacy/example/SKILL.md` | ported Go-binary contract spec — addendum verdict INCLUDED | per-file sha256 |

**Expected legacy/ tree** (post-archive) — **7 TOP-LEVEL entries** (NOT recursive): `legacy/` → `agents/` (parent of 5 BASE-1..5 + STACK-3) + `daemon/` (parent of STACK-1 + STACK-2) + `tests/` (STACK-4) + `test/` (STACK-5..7) + `docs/` (STACK-9..10) + `example/` (STACK-11) + `.agents/` (STACK-8) = **7 top-level entries** (3 dirs-from-base-content, 4 dirs-from-stack, plus the 2 file-only tops `docs/` and `test/` and `example/` — actually all 7 are dirs, none are direct files at the top level). The executor confirms by `find legacy -mindepth 1 -maxdepth 1 | sort` (W1 fix: NOT recursive — without `-maxdepth 1` the `find` recurses into subdirs and returns ~88 entries, which would falsely trip the count check).

### Touch set (edited-in-place) — git history is primary truth

These files are EDITED in Phase C, not archived. Pre-edit byte content lives in git history at worktree HEAD `885a19d4f`. Phase A may optionally capture a `pre-edit-snapshots/<file>.before.txt` diff artifact as a secondary mitigation, but git is sufficient.

| Touch ID | Live path | Addendum-cited lines | Edit description (Phase C) | Phase A action |
|----------|-----------|----------------------|------------------------------|----------------|
| **TOUCH-1** | `daemon/manager.py` | `:78` (import), `:1419-1435` (engine + repo bootstrap, cluster within addendum's cited `:1420-1436`), `:2895-2910` (`opencode_registry` accessor property), `:3325-3338` (startup-recovery DISABLED block — dead code path), `:5948-5956` (idle-session eviction), `:6748-6754` (jsonb targets `opencode_sessions.latest_response` + `opencode_sessions.questions` — schema-cleanup concern), `:13033` (shutdown-step name), `:13078-13103` (`_shutdown_opencode_registry` body + teardown) | Drop imports, drop init block, drop accessor, drop dead recovery, drop eviction block, remove from jsonb target list, remove from shutdown steps list, drop method body | Optional: capture pre-PR `git show HEAD:daemon/manager.py` into `pre-edit-snapshots/manager.py.before.txt` |
| **TOUCH-2** | `daemon/tools/instance.py` | `:155-167` (grant-map docstring + table), `:259` (`from .external_opencode import create_opencode_tools`), `:5815-5818` (factory-call site in tool assembly) | Drop `external_opencode` from grant-map, drop import, drop factory call | Optional: pre-edit snapshot |
| **TOUCH-3** | `daemon/tools/_tool_registry.py` | `:603` (category string mapping `"external_opencode" → "daemon.tools.external_opencode"`), `:764-771` (8 tool-name registrations: `external_opencode_abort_session`, `_answer_question`, `_get_status`, `_init_session`, `_resume_session`, `_send_message`, `_wait_any`, `_wait_for_result`) | Drop category mapping, drop all 8 tool-name registrations | Optional: pre-edit snapshot |
| **TOUCH-4** | `Makefile` | `:174` (comment referencing `opencode_sessions.db` in backup description), `:186` (FILES list entry `opencode_sessions.db opencode_sessions.db-wal opencode_sessions.db-shm`) | Drop `opencode_sessions.db*` FILES entries + comment update | Optional: pre-edit snapshot |
| **TOUCH-5** | `tests/unit/test_coder_agent.py` | `:40-43` (contamination-guard test fixture entry `"external_opencode": [...]` in the grant-map snapshot) | Drop the `external_opencode` key from the grant-map snapshot (kept-after: `tests/unit/test_coder_agent.py` continues to pass because coder is opencode-free; the contamination guard survives but the fixture no longer mentions `external_opencode`) | Optional: pre-edit snapshot |
| **TOUCH-6** | `tests/test_innate_skills_refactoring.py` | (multiple) | Drop opencode-referenced test cases (Phase B also touches this file for version-pin edits — coordinated edit) | Optional: pre-edit snapshot |
| **TOUCH-7** | `tests/migration/test_jsonb_migration.py` | `:70-107` (jsonb migration test for opencode_sessions table) | Drop or rewrite test (depends on whether the jsonb target list also drops `opencode_sessions` columns — coupled with TOUCH-1) | Optional: pre-edit snapshot |
| **TOUCH-8** | `tests/helpers/send_message_fixtures.py` | `:68` (mock site) | Drop opencode mock | Optional: pre-edit snapshot |
| **TOUCH-9** | `tests/test_manager_shutdown.py` | `:126` (mock site) | Drop opencode mock | Optional: pre-edit snapshot |
| **TOUCH-10** | `tests/integration/test_spawn_default_unchanged.py` | `:163` (mock site) | Drop opencode mock | Optional: pre-edit snapshot |
| **TOUCH-11** | `pyproject.toml` | `:88` (`integration: integration tests that require a live OpenCode server`) | Rephrase to drop OpenCode mention (or drop the integration marker entirely if no other integration-tier surface needs it) | Optional: pre-edit snapshot |
| **TOUCH-12** | `.env.example` | `:39` (comment about `OPENAI_MODEL_KEYWORDS` for opencode prompts) | Rephrase comment to drop `opencode` mention **(mandatory per R10 §11.2 — was optional, now REQUIRED)** | Optional: pre-edit snapshot |
| **TOUCH-13** | `daemon/services/keyword_extraction.py` | (docstring/comments only) | Comment rephrase only — module STAYS (live callers per D-A7) **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-14** | `daemon/services/context_injection.py` | `:541, :846` | Comment rephrase only — module STAYS (live callers per D-A7) **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-15** | `daemon/services/context_messages.py` | (comments/docstring) | Comment rephrase only **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-16** | `daemon/services/critical_note_gate.py` | `:8` | Comment rephrase only **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-17** | `daemon/services/waiting_children_watchdog.py` | (comments/docstring) | Comment rephrase only **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-18** | `daemon/loader.py` | `:226` | Comment rephrase only — module STAYS **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-19** | `daemon/config.py` | `:168` (comment about keyword-extraction for opencode prompts) | Comment rephrase only — module STAYS **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-20** | `daemon/repositories/task/repository.py` | `:1777` | Comment rephrase only **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-21** | `agents/explorer/soul.md` | (cosmetic one-liner) | Drop or rephrase single opencode mention **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-22** | `agents/devops/soul.md` | (cosmetic one-liner) | Drop or rephrase single opencode mention **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-23** | `agents/_prompt_system/knowledge.md` | `:96, :106` (2 lines describing anti-pattern "use internal explore() not opencode-skill for codebase exploration — verified present, live prompt-system prose every agent reads) | Rephrase to drop opencode-skill mention; preserve the explore()-mandatory rule's general intent **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-24** | `docs/agents.md` | §2 | Doc refresh **(mandatory per R10 §11.2 — every active-tree doc row that is not historical/audit)** | Optional: pre-edit snapshot |
| **TOUCH-25** | `docs/skill-evolution.md` | `:174` | Doc refresh **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |
| **TOUCH-26** | `README.md` | (opencode references throughout) | Doc refresh **(mandatory per R10 §11.2)** | Optional: pre-edit snapshot |

### LEAVE ALONE (final)

The following do NOT move, do NOT edit:

| Path / pattern | Reason |
|----------------|--------|
| `agents/coder/` | opencode-free by design; preserved as-is |
| `daemon/services/keyword_extraction.py` (module itself) | live-shared infra (callers: `attestation_report_judge.py:35-328`, `graph.py:10794`, `plugin_subsystem/opendesign/generate.py:650`) |
| `daemon/services/context_injection.py` (module itself) | live-shared infra (same callers) |
| `docs/mcp-kb-usage.md` | upstream product (D6); not opencode-specific |
| `.agents/*/RESULTS/` + `.agents/*/LESSONS/` | agent test/historical result dirs; OUT of scope |
| `data/`, `data_dev/` DB files (incl. stray `opencode_sessions.db` at repo root if present) | **NOTE in plan/report** — orphan test artifact; boot-recreate dies with the code so it will not be re-created; do NOT delete (may be someone's local DB) |
| `.inspiration-projects/opencode_skill_src/` | gitignored; not in repo |

### Repo-root stray DB artifact (specific addendum note)

If `opencode_sessions.db` (or its WAL/SHM sidecars) appears at the repo root at execution time, record its presence in `evidence/phaseA/stray-db-artifacts.txt` and **DO NOT DELETE** — it may be a local DB someone created during testing. Boot-recreate dies with the code, so the file becomes inert post-Phase-C. Mentioned in the mission report for visibility.

> **⛔ S7 caution: NEVER run `git clean -fdx` in the worktree.** Doing so would erase the orphan `opencode_sessions.db` (the record-and-do-not-delete artifact above) AND the untracked evidence/ files AND any other untracked test/dev artifacts that the rollback story depends on. The plan's rollback uses `git checkout -- legacy/ evidence/ 2>/dev/null || true` + `git clean -fd legacy/ evidence/` (scoped to those two paths) — NOT `git clean -fdx` (which would wipe `.gitignore`-untracked files anywhere in the worktree). Phase A's own rollback IS scoped correctly (per §Phase Rollback below); this caution is for the executor and any future reader who might be tempted to "clean up the worktree".

### Executing-extension protocol (FINAL — no growth)

The executor does NOT extend the move-set. If a file is found at execution time that the addendum did not classify as part of the stack or touch set:

1. Halt Phase A.
2. Record the unexpected file path in `evidence/phaseA/anomalies.txt` with sha256 + any context.
3. Engage the user via dispatcher; do NOT silently archive or edit.
4. Resolution updates this plan or supersedes with a follow-up addendum.


## File Inventory

### Read-only inputs (verified 2026-10-13 in worktree)

**Base agent dirs (5) — pre-overwrite content:**
- `agents/approver/` — `memory.md`, `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `workflow.md` (6 files)
- `agents/developer/` — `growth.md`, `memory.md`, `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `user.md`, `workflow.md` (8 files; growth.md, user.md are v2-lacks)
- `agents/planner/` — `memory.md`, `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `workflow.md` (6 files)
- `agents/reviewer/` — `memory.md`, `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `workflow.md` (6 files)
- `agents/tidier/` — `memory.md`, `meta.json`, `rule.md`, `soul.md`, `tools_note.md`, `workflow.md` (6 files)

**Opencode stack move-set (FINAL — addendum-resolved, verified present in worktree):**

> One retirement unit with two named roles; the executor copies each path verbatim into the matching mirror target (per the BEFORE/AFTER MAP). Reference: `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP" for the consolidated map and verification recipe.

- `daemon/opencode/` (dir, 8 `.py` files including `__init__.py`: `client.py`, `constants.py`, `registry.py`, `repository.py`, `server.py`, `session_manager.py`, `state.py`) — **STACK-1**.
- `daemon/tools/external_opencode.py` (file) — **STACK-2**.
- `agents/_prompt_system/innate-skills/opencode/` (dir, `skill.md` + `skill.md.bak`) — **STACK-3**.
- `tests/opencode/` (dir, 12 `.py` files including `__init__.py`, `conftest.py`) — **STACK-4**.
- `test/packs/opencode_native_tools_unit_test.sh` (file) — **STACK-5**.
- `test/packs/ab/top_opencode_unit_test.sh` (file) — **STACK-6** (verified present in `test/packs/ab/` listing).
- `test/packs/regression_opencode_e2e_test.sh` (file) — **STACK-7**.
- `.agents/shared/planning/opencode-native-tools/` (dir, 7 `.md` files: `go-to-python-parity-check.md`, `plan-overview.md`, `phase1-plan.md` … `phase5-plan.md`) — **STACK-8**.
- `docs/bugs/opencode-session-manager-memory-leak.md` (file) — **STACK-9**.
- `docs/bugs/external-opencode-wait-latency.md` (file) — **STACK-10**.
- `example/SKILL.md` (file, 5489 bytes; the ported Go binary's contract spec — `name: opencode-skill`) — **STACK-11** (D-A2 addendum-resolved: INCLUDED).

### Writes produced by this phase (git add committed with the mission PR)

> **Parameterized archive inventory** — entries below follow the BEFORE/AFTER MAP (§"BEFORE/AFTER MAP"). The map is FINAL (addendum-resolved); any unexpected file at execute time triggers the executing-extension protocol (HALT + engage user).

**Created dirs (new):**
- `legacy/`
- `legacy/agents/{approver,developer,planner,reviewer,tidier}/`
- `legacy/daemon/opencode/`
- `legacy/daemon/tools/` *(parent of STACK-2 only — NOT a full mirror of `daemon/tools/`)*
- `legacy/agents/_prompt_system/innate-skills/opencode/`
- `legacy/tests/opencode/`
- `legacy/test/packs/`
- `legacy/test/packs/ab/`
- `legacy/docs/bugs/`
- `legacy/.agents/shared/planning/opencode-native-tools/`

**Created files (file-level archive targets):**

**Base agent CONTENT** (5 base dir pre-overwrite snapshots):
- `legacy/agents/<name>/<all-current-base-files>` per inventory below
  - `legacy/agents/approver/{memory.md, meta.json, rule.md, soul.md, tools_note.md, workflow.md}` (6 files)
  - `legacy/agents/developer/{growth.md, memory.md, meta.json, rule.md, soul.md, tools_note.md, user.md, workflow.md}` (8 files; `growth.md`, `user.md` are v2-lacks)
  - `legacy/agents/planner/{memory.md, meta.json, rule.md, soul.md, tools_note.md, workflow.md}` (6 files)
  - `legacy/agents/reviewer/{memory.md, meta.json, rule.md, soul.md, tools_note.md, workflow.md}` (6 files)
  - `legacy/agents/tidier/{memory.md, meta.json, rule.md, soul.md, tools_note.md, workflow.md}` (6 files)

**Opencode stack archive — FINAL, addendum-resolved (one move, two roles in Notes):**

| Map ID | Archive target under `legacy/` | Notes (stream / role / coupling) |
|--------|--------------------------------|----------------------------------|
| STACK-1 | `legacy/daemon/opencode/{__init__.py, client.py, constants.py, registry.py, repository.py, server.py, session_manager.py, state.py}` | one retirement unit, two roles: protocol/comm dispatch (`server.py` action loop, Go→Python port of `server.go:230-507`) + session/API lifecycle (`client.py` HTTP transport + `session_manager.py` state machine + `state.py` enum + `registry.py` in-memory map + `repository.py` persistence + `constants.py` + `__init__.py` re-exports). Sole coupled consumer: STACK-2. |
| STACK-2 | `legacy/daemon/tools/external_opencode.py` | sole comm-consumer shim; `create_opencode_tools()` factory + 8 `external_opencode_*` LangChain `@tool` defs; consumes `daemon.opencode.server.external_opencode_send_message` (comm dispatch) AND `daemon.opencode.client.OpenCodeClient` (session/api transport) — bridges both roles to the agent runtime. |
| STACK-3 | `legacy/agents/_prompt_system/innate-skills/opencode/{skill.md, skill.md.bak}` | agent-facing surface — innate skill prompt + dotfile backup. |
| STACK-4 | `legacy/tests/opencode/{__init__.py, conftest.py, test_client.py, test_error_propagation_edge_cases.py, test_integration.py, test_registry.py, test_repository.py, test_server.py, test_session_manager.py, test_state.py, test_table_creation.py, test_tools.py}` | uniform single tree, 12 files (re-verify file count at execute time). |
| STACK-5 | `legacy/test/packs/opencode_native_tools_unit_test.sh` | pack wrapper (single file). |
| STACK-6 | `legacy/test/packs/ab/top_opencode_unit_test.sh` | pack wrapper (AB dir, single file). |
| STACK-7 | `legacy/test/packs/regression_opencode_e2e_test.sh` | e2e regression pack wrapper. |
| STACK-8 | `legacy/.agents/shared/planning/opencode-native-tools/{plan-overview.md, go-to-python-parity-check.md, phase1-plan.md, phase2-plan.md, phase3-plan.md, phase4-plan.md, phase5-plan.md}` | mission planning history (D-A1 placement locked — addendum-confirmed). |
| STACK-9 | `legacy/docs/bugs/opencode-session-manager-memory-leak.md` | opencode-era bug doc. |
| STACK-10 | `legacy/docs/bugs/external-opencode-wait-latency.md` | opencode-era bug doc. |
| STACK-11 *(D-A2 FINAL-resolved)* | `legacy/example/SKILL.md` | the ported Go binary's contract spec; **INCLUDED** per addendum. The Step 2 preflight `grep -i opencode example/SKILL.md` resolves `ARCHIVE_EXAMPLE_SKILL=1` unconditionally — the executor skips the conditional gate. |

**(No `[ADDENDUM-DERIVED ENTRIES PLACEHOLDER]`** — the move-set is FINAL and addendum-resolved. Executing-extension protocol (see §"BEFORE/AFTER MAP" end) governs any unexpected file at execute time.)

**Evidence files (do NOT git-commit; written to plan-evidence dir which is gitignored if `.gitignore` excludes `.agents/` it doesn't — see D-A1 note in `legacy/` decision above):**

> **S8 clarifying line:** the empty `evidence/phaseA/` scaffold directory is **INTENTIONALLY UNPOPULATED at planning time** — it is populated during execution by the Steps below. The presence of the empty `evidence/phaseA/` directory in the planning dir at planning time is EXPECTED (the executor's `mkdir -p` calls in Steps 1–3 create the per-section subdirs). The same applies to phaseB's `evidence/phaseB/` and the source-tree/legacy-build subdirs — they are execution-time artifacts, not pre-existing plan content.
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/<dir>.manifest.txt` — pre-archive manifest per source dir
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt` — pre-archive manifest for single-file archive entries (STACK-2, STACK-3 [skill.md + .bak], STACK-5..7, STACK-9..10, STACK-8 [7 .md files], STACK-11 — 16 lines total)
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt` — post-archive manifest of legacy/ tree
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.by-stream.md` — post-archive manifest **partitioned by retirement stream** (comm-service | session/API-lane | shared | planning | bug doc | conditional), enabling the verification pass to confirm each stream's archive is complete independently
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/<dir>.diff.txt` — per-dir source-vs-legacy sha256 diff (must be empty)
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/single-files.diff.txt` — per-file source-vs-legacy sha256 diff (must be empty/all-OK)
- `.agents/shared/planning/agent-base-v2/evidence/phaseA/pre-archive-map.md` — the resolved pre-archive map (copy of §"Pre-Archive Map" with execution-time adjustments noted; this is the before/after map deliverable for both streams)


### Not touched by this phase

- `agents/<name>[v2]/*` (binding D4)
- `agents/<name>/` content (Phase B is the writer; Phase A is the READER only — no in-place mutations)
- `daemon/opencode/` source (Phase A only MIRRORS to legacy/; deletion is Phase C)
- `agents/_prompt_system/innate-skills/opencode/` source (mirrored only)
- Live daemon, DB, release tree, install dir — D8 forbids all of these

## Dependencies & Ordering

- **Upstream**: nothing (Phase A is the first executable phase).
- **Downstream**: Phase B depends on Phase A `legacy/` existing (so that any content-replace rollback can pull base files back from `legacy/agents/<name>/`). Phase B also depends on Phase A having captured the pre-overwrite base content for the v2-stays-untouched proof (separate proof, both phases need fresh manifests).
- **Sibling**: Phase C, D, E run after Phase B. Phase E reads Phase A evidence files.
- **ENGINEERING-CRITICAL ORDERING CONSTRAINT (D-A8, addendum-issued):** while the 5 old-base agents still declare `"innate_skills": ["opencode", "chart", "todo"]` in their CURRENT base `meta.json` (worktree HEAD `885a19d4f`), the innate-skills loader MUST resolve the `opencode` innate skill — both its prompt dir (`agents/_prompt_system/innate-skills/opencode/`) and its grant-map entry (`daemon/tools/instance.py:162 INNATE_SKILL_TOOL_CATEGORIES`). **Stack removal (Phase C) MUST land WITH or AFTER Phase B's base := v2 retirement**, never before. Phase A's pure-copy archive is safe anytime (it does not mutate any live tree path). The constraint binds Phase C, not Phase A.
- **Practical sequencing implication**: if Phase A and Phase B execute together in the same PR as Phase C (mission co-located model), the constraint is satisfied because Phase B retires the base `meta.json` BEFORE Phase C removes the stack. If they execute as separate PRs, Phase C MUST NOT land until Phase B has been promoted.
- **Stray DB artifact awareness**: a repo-root `opencode_sessions.db` (or WAL/SHM sidecars) may exist from prior testing; Phase A records its presence in `evidence/phaseA/stray-db-artifacts.txt` and does NOT delete it. The file becomes inert post-Phase-C because the boot-recreate logic dies with the code. Mention in the mission report for visibility.

## Step-by-Step Operations

> Convention: run all commands inside worktree `/home/nea/ensemble-src-wt-agent-base-v2`. Use ONE hash tool (`shasum -a 256`). All `legacy/` paths are relative to worktree root. **FAKE_HOME fence mandatory** (project critical note 2026-10-12). Live install paths must NEVER be resolved by any script that this plan runs.

### Step 1 — Worktree + FAKE_HOME fence

```bash
cd /home/nea/ensemble-src-wt-agent-base-v2
git rev-parse --abbrev-ref HEAD         # MUST print: feature/agent-base-v2
git rev-parse HEAD                       # MUST print: 885a19d4f45a3403a1bc615ec0f393630c938c84
git status --porcelain                   # MUST be empty
# FAKE_HOME fence (project critical note 2026-10-12 — INCIDENT pattern)
export FAKE_HOME="$(mktemp -d -t agent-base-v2-XXXXXX)"
trap 'cd - >/dev/null 2>&1; rm -rf "$FAKE_HOME"' EXIT
# Escape guard: any script under THIS plan that touches $HOME-abspath MUST instead use
# $FAKE_HOME or a sibling-anchored absolute path inside the worktree.
```

### Step 2 — Preflight: D-A2 example/SKILL.md (FINAL — addendum-resolved: unconditional INCLUDE)

```bash
# D-A2 addendum-resolved: example/SKILL.md is the ported Go binary's contract spec
# (name: opencode-skill, metadata: version 1.2.0). It is IN-SCOPE for the legacy archive
# unconditionally. The grep is still executed to capture the verbatim opencode-keyword
# presence as audit evidence; resolution is forced to 1.
grep -i 'opencode' example/SKILL.md | head -3 \
    >> .agents/shared/planning/agent-base-v2/evidence/phaseA/d-a2-evidence.txt
ARCHIVE_EXAMPLE_SKILL=1
echo "D-A2 (FINAL — addendum-resolved): example/SKILL.md is the ported Go-binary contract spec → INCLUDE in legacy/" >&2
echo "ARCHIVE_EXAMPLE_SKILL=$ARCHIVE_EXAMPLE_SKILL" \
    > .agents/shared/planning/agent-base-v2/evidence/phaseA/d-a2-resolution.txt

# D-A8 ordering-constraint evidence (recorded in Phase A for Phase C executor visibility)
echo "D-A8 ordering constraint: Phase C stack removal MUST land WITH or AFTER Phase B base := v2 retirement. Rationale: while base meta.json declares innate_skills: [opencode,...], the loader must resolve agents/_prompt_system/innate-skills/opencode/ AND the INNATE_SKILL_TOOL_CATEGORIES entry at daemon/tools/instance.py:162. Phase A pure-copy archive is safe anytime. " \
    > .agents/shared/planning/agent-base-v2/evidence/phaseA/ordering-constraint.txt

# D-A7 boundary-exceptions schema — created empty per Verdict 2 (no retained-live surfaces)
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseA/boundary-exceptions
: > .agents/shared/planning/agent-base-v2/evidence/phaseA/boundary-exceptions/.gitkeep
echo "D-A7 (FINAL — addendum-resolved): EMPTY retained-live state per Verdict 2 (coder opencode-free; no callers). Schema retained for future-proofing. Any non-empty entry indicates a live-used surface found at execute time and flags for user decision." \
    > .agents/shared/planning/agent-base-v2/evidence/phaseA/boundary-exceptions/README.md

# anomalies.txt init (empty per FINAL verdict; any unexpected opencode-tied file surfaces triggers HALT)
: > .agents/shared/planning/agent-base-v2/evidence/phaseA/anomalies.txt
```

### Step 3 — Pre-archive manifest: source trees (used for diff verification)

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/opencode-modules

# Per the D-A3 recipe: find → sort → sha256 each file → "<sha>  <relpath>" lines.
# Single source of truth per dir; excluded __pycache__/ matches lib.sh:1928 _tree_manifest.
_make_manifest() {
    local src="$1"; local out="$2"
    ( cd "$src" && find . -type f \
        -not -path './__pycache__/*' \
        -not -name '*.pyc' \
        | sed 's|^\./||' | sort \
        | while IFS= read -r f; do
            [ -n "$f" ] || continue
            printf '%s  %s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$f"
        done
    ) > "$out"
    shasum -a 256 "$out" | awk '{print $1}' > "$out.sha256"
}

# Base agent dirs (5)
for name in approver developer planner reviewer tidier; do
    _make_manifest "agents/$name" ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/agents-$name.manifest.txt"
done

# Opencode stack top-level dirs/files
for p in daemon/opencode tests/opencode; do
    _make_manifest "$p" ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/${p//\//-}.manifest.txt"
done
# Opencode stack per-module granularity (8 modules — enables per-role verification)
for m in __init__.py client.py constants.py registry.py repository.py server.py session_manager.py state.py; do
    sha="$(shasum -a 256 "daemon/opencode/$m" | awk '{print $1}')"
    printf '%s  %s\n' "$sha" "$m" \
        > ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/opencode-modules/$m.manifest.txt"
    shasum -a 256 ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/opencode-modules/$m.manifest.txt" \
        | awk '{print $1}' \
        > ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/opencode-modules/$m.manifest.txt.sha256"
done

# Innate-skills dir contains dotfile .bak — find handles it (per task context note)
_make_manifest agents/_prompt_system/innate-skills/opencode \
    ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/agents-_prompt_system-innate-skills-opencode.manifest.txt"

# Per-file sha256 of each opencode stack top-level file path (single-file dirs/entries)
for f in daemon/tools/external_opencode.py \
         test/packs/opencode_native_tools_unit_test.sh \
         test/packs/ab/top_opencode_unit_test.sh \
         test/packs/regression_opencode_e2e_test.sh \
         agents/_prompt_system/innate-skills/opencode/skill.md \
         agents/_prompt_system/innate-skills/opencode/skill.md.bak \
         docs/bugs/opencode-session-manager-memory-leak.md \
         docs/bugs/external-opencode-wait-latency.md \
         .agents/shared/planning/opencode-native-tools/plan-overview.md \
         .agents/shared/planning/opencode-native-tools/go-to-python-parity-check.md \
         .agents/shared/planning/opencode-native-tools/phase1-plan.md \
         .agents/shared/planning/opencode-native-tools/phase2-plan.md \
         .agents/shared/planning/opencode-native-tools/phase3-plan.md \
         .agents/shared/planning/opencode-native-tools/phase4-plan.md \
         .agents/shared/planning/opencode-native-tools/phase5-plan.md; do
    mkdir -p ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/$(dirname "$f")"
    sha="$(shasum -a 256 "$f" | awk '{print $1}')"
    printf '%s  %s\n' "$sha" "$f" \
        >> ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt"
done

# example/SKILL.md — UNCONDITIONAL per D-A2 addendum-resolved
sha="$(shasum -a 256 example/SKILL.md | awk '{print $1}')"
printf '%s  %s\n' "$sha" "example/SKILL.md" \
    >> ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt"

sort -k2 ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt" \
    -o ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt"
shasum -a 256 ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt" \
    | awk '{print $1}' \
    > ".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt.sha256"

# Repo-root stray DB artifact check (record-and-do-not-delete)
stray_db_check() {
    : > .agents/shared/planning/agent-base-v2/evidence/phaseA/stray-db-artifacts.txt
    for f in opencode_sessions.db opencode_sessions.db-wal opencode_sessions.db-shm; do
        if [ -e "$f" ]; then
            realpath "$f" >> .agents/shared/planning/agent-base-v2/evidence/phaseA/stray-db-artifacts.txt
            shasum -a 256 "$f" >> .agents/shared/planning/agent-base-v2/evidence/phaseA/stray-db-artifacts.txt
            echo "NOTICE: stray DB artifact at repo root: $(realpath "$f") — recorded, NOT deleted (per addendum note)" >&2
        fi
    done
}
stray_db_check
```

Per-step verification:
- All 5 base + 4 move-set dir targets produce a non-empty manifest.
- All 8 `daemon/opencode/` per-module manifests produce a single `<sha>  <module-name>` line each.
- `single-files.manifest.txt` has exactly 16 lines (15 always-included + `example/SKILL.md` unconditional).
- `stray-db-artifacts.txt` exists (empty if no stray DB at repo root; otherwise contains the recorded path + sha).
- `d-a2-resolution.txt` records `ARCHIVE_EXAMPLE_SKILL=1`.
- `boundary-exceptions/.gitkeep` exists; `README.md` documents the FINAL verdict.
- `ordering-constraint.txt` exists with the D-A8 constraint text.

### Step 4 — Create legacy/ skeleton

```bash
mkdir -p legacy
mkdir -p legacy/agents
mkdir -p legacy/daemon/tools
mkdir -p legacy/agents/_prompt_system/innate-skills
mkdir -p legacy/tests
mkdir -p legacy/test/packs/ab
mkdir -p legacy/docs/bugs
mkdir -p legacy/.agents/shared/planning
mkdir -p legacy/example   # D-A2 FINAL-resolved: example/SKILL.md archived unconditionally
```

Per-step verification:
- `find legacy -type d` lists all created dirs; no stray files yet.

### Step 5 — Archive base agent contents (5 dirs)

```bash
for name in approver developer planner reviewer tidier; do
    mkdir -p "legacy/agents/$name"
    # cp -pR mirrors permissions + timestamps; this is content-archival, not payload-staging
    cp -pR "agents/$name/." "legacy/agents/$name/"
done
```

Per-step verification (after the loop):
```bash
for name in approver developer planner reviewer tidier; do
    diff -r "agents/$name" "legacy/agents/$name" || echo "MISMATCH for $name"
done
# Expected: NO output (all dir-compare diffs match byte-identical)
```

### Step 6 — Archive opencode stack (FINAL — addendum-resolved)

```bash
# STACK-1: daemon/opencode/ (one retirement unit, two roles: protocol/comm + session/api)
mkdir -p legacy/daemon/opencode
cp -pR daemon/opencode/. legacy/daemon/opencode/

# STACK-2: daemon/tools/external_opencode.py (sole comm-consumer shim)
cp -p daemon/tools/external_opencode.py legacy/daemon/tools/external_opencode.py

# STACK-3: agents/_prompt_system/innate-skills/opencode/ (skill prompt + .bak dotfile)
mkdir -p legacy/agents/_prompt_system/innate-skills/opencode
cp -pR agents/_prompt_system/innate-skills/opencode/. legacy/agents/_prompt_system/innate-skills/opencode/

# STACK-4: tests/opencode/ (uniform 12-file tree)
mkdir -p legacy/tests/opencode
cp -pR tests/opencode/. legacy/tests/opencode/

# STACK-5..7: test/packs/* (3 sh files — keep under legacy/test/packs/<same-name>)
cp -p test/packs/opencode_native_tools_unit_test.sh legacy/test/packs/opencode_native_tools_unit_test.sh
cp -p test/packs/ab/top_opencode_unit_test.sh legacy/test/packs/ab/top_opencode_unit_test.sh
cp -p test/packs/regression_opencode_e2e_test.sh legacy/test/packs/regression_opencode_e2e_test.sh

# STACK-8: D-A1 addendum-locked placement at legacy/.agents/shared/planning/opencode-native-tools/
mkdir -p legacy/.agents/shared/planning/opencode-native-tools
for f in plan-overview.md go-to-python-parity-check.md phase1-plan.md phase2-plan.md phase3-plan.md phase4-plan.md phase5-plan.md; do
    cp -p ".agents/shared/planning/opencode-native-tools/$f" "legacy/.agents/shared/planning/opencode-native-tools/$f"
done

# STACK-9..10: docs/bugs/opencode-* (2 bug docs)
cp -p docs/bugs/opencode-session-manager-memory-leak.md legacy/docs/bugs/
cp -p docs/bugs/external-opencode-wait-latency.md legacy/docs/bugs/

# STACK-11: example/SKILL.md UNCONDITIONAL (D-A2 addendum-resolved: ported Go binary's contract spec)
cp -p example/SKILL.md legacy/example/SKILL.md
```

Per-step verification:
```bash
# Diff each archived pair (must be empty). Each pair uses dir diff or file diff per type.
for pair in \
    "daemon/opencode:legacy/daemon/opencode" \
    "daemon/tools/external_opencode.py:legacy/daemon/tools/external_opencode.py" \
    "agents/_prompt_system/innate-skills/opencode:legacy/agents/_prompt_system/innate-skills/opencode" \
    "tests/opencode:legacy/tests/opencode" \
    "test/packs/opencode_native_tools_unit_test.sh:legacy/test/packs/opencode_native_tools_unit_test.sh" \
    "test/packs/ab/top_opencode_unit_test.sh:legacy/test/packs/ab/top_opencode_unit_test.sh" \
    "test/packs/regression_opencode_e2e_test.sh:legacy/test/packs/regression_opencode_e2e_test.sh" \
    ".agents/shared/planning/opencode-native-tools:legacy/.agents/shared/planning/opencode-native-tools" \
    "docs/bugs/opencode-session-manager-memory-leak.md:legacy/docs/bugs/opencode-session-manager-memory-leak.md" \
    "docs/bugs/external-opencode-wait-latency.md:legacy/docs/bugs/external-opencode-wait-latency.md" \
    "example/SKILL.md:legacy/example/SKILL.md"; do
    src="${pair%%:*}"; dst="${pair##*:}"
    if [ -d "$src" ]; then
        diff -r "$src" "$dst" || echo "MISMATCH: $src vs $dst"
    else
        diff "$src" "$dst" || echo "MISMATCH: $src vs $dst"
    fi
done
# Expected: NO output (all 11 pairs byte-identical)
```

### Step 7 — Post-archive manifest: legacy/ tree + by-stream partition

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build
_make_manifest legacy \
    .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt
TREE_DIGEST="$(shasum -a 256 .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt | awk '{print $1}')"
echo "$TREE_DIGEST" > .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.sha256

# By-stream partition: enables verification that each role's archive is complete independently.
# Each role/stream section lists its archive entries (count + paths).
cat > .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.by-stream.md <<'EOF'
# Legacy tree partitioned by retirement stream / role (FINAL — addendum-resolved)

Generated post-archive; reflects the BEFORE/AFTER MAP exactly.

## Base-content (5 dirs — pre-overwrite snapshots for Phase B rollback source)
EOF
for name in approver developer planner reviewer tidier; do
    cnt=$(find "legacy/agents/$name" -type f | wc -l | tr -d ' ')
    echo "- $name: $cnt files under legacy/agents/$name/" \
        >> .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.by-stream.md
done

cat >> .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.by-stream.md <<'EOF'

## Opencode stack — protocol/comm role (server.py + sole consumer shim)
- daemon/opencode/server.py (protocol/comm dispatch)
- daemon/tools/external_opencode.py (sole comm-consumer shim)

## Opencode stack — session/API role (lifecycle + transport + state + repo)
- daemon/opencode/__init__.py
- daemon/opencode/client.py
- daemon/opencode/constants.py
- daemon/opencode/registry.py
- daemon/opencode/repository.py
- daemon/opencode/session_manager.py
- daemon/opencode/state.py

## Shared/infrastructure (cross-role)
- agents/_prompt_system/innate-skills/opencode/skill.md
- agents/_prompt_system/innate-skills/opencode/skill.md.bak

## Tests + test packs
- tests/opencode/ (12 files)
- test/packs/opencode_native_tools_unit_test.sh
- test/packs/ab/top_opencode_unit_test.sh
- test/packs/regression_opencode_e2e_test.sh

## Mission planning history (D-A1 addendum-locked)
- .agents/shared/planning/opencode-native-tools/ (7 .md files)

## Bug docs (opencode-era)
- docs/bugs/opencode-session-manager-memory-leak.md
- docs/bugs/external-opencode-wait-latency.md

## Ported Go-binary contract spec (D-A2 addendum-resolved INCLUDED)
- example/SKILL.md

## Retained-live (boundary-rule exception) — EMPTY per Verdict 2
(none — `boundary-exceptions/` directory created empty as schema future-proofing)
EOF
```

Per-step verification:
- `legacy-tree.manifest.txt` is non-empty; `legacy-tree.sha256` is 64 hex chars.
- `legacy-tree.by-stream.md` lists every section above; the "Retained-live" section is empty.

### Step 8 — Diff: source-trees vs legacy-tree (verification)

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseA/diffs

# Per-dir diff (must be empty). For each dir-based source manifest, compare against
# the legacy build manifest filtered to that source's prefix.
for name in approver developer planner reviewer tidier; do
    src_manifest=".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/agents-$name.manifest.txt"
    diff_file=".agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/agents-$name.diff.txt"
    awk -v n="$name" '$2 ~ "^agents/"n"/" { sub("^agents/"n"/", "", $2); print $1"  "$2 }' \
        .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt \
        | sort -k2 > "$diff_file.filtered"
    sort -k2 "$src_manifest" > "$diff_file.srcsorted"
    diff "$diff_file.srcsorted" "$diff_file.filtered" > "$diff_file" || true
done

# Per the opencode stack:
for src in daemon/opencode tests/opencode \
           agents/_prompt_system/innate-skills/opencode; do
    safe="$(echo "$src" | tr '/' '-')"
    src_manifest=".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/${safe}.manifest.txt"
    diff_file=".agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/${safe}.diff.txt"
    awk -v s="$src" '$2 ~ "^"s"/" { sub("^"s"/", "", $2); print $1"  "$2 }' \
        .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt \
        | sort -k2 > "$diff_file.filtered"
    sort -k2 "$src_manifest" > "$diff_file.srcsorted"
    diff "$diff_file.srcsorted" "$diff_file.filtered" > "$diff_file" || true
done

# Per-module diff for the 8 daemon/opencode modules (per-role granularity)
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/opencode-modules
for m in __init__.py client.py constants.py registry.py repository.py server.py session_manager.py state.py; do
    src_sha="$(awk '{print $1}' .agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/opencode-modules/$m.manifest.txt)"
    leg_sha="$(awk -v p="legacy/daemon/opencode/$m" '$2==p {print $1; exit}' \
        .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt)"
    diff_file=".agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/opencode-modules/$m.diff.txt"
    if [ "$src_sha" = "$leg_sha" ]; then
        echo "OK  daemon/opencode/$m  src=$src_sha  leg=$leg_sha" > "$diff_file"
    else
        echo "DIFF daemon/opencode/$m  src=$src_sha  leg=$leg_sha" > "$diff_file"
    fi
done

# Single-file diffs: read source sha from source-trees manifest, find legacy line with same relpath.
: > .agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/single-files.diff.txt
SINGLE=".agents/shared/planning/agent-base-v2/evidence/phaseA/source-trees/single-files.manifest.txt"
while IFS= read -r sha_rel; do
    [ -z "$sha_rel" ] && continue
    src_sha="$(echo "$sha_rel" | awk '{print $1}')"
    relpath="$(echo "$sha_rel" | awk '{$1=""; sub(/^ /, ""); print}')"
    legacy_rel="$relpath"
    case "$relpath" in
        daemon/*)         legacy_rel="legacy/$relpath" ;;
        tests/opencode/*) legacy_rel="legacy/$relpath" ;;
        agents/_prompt_system/*) legacy_rel="legacy/$relpath" ;;
        test/packs/*)     legacy_rel="legacy/$relpath" ;;
        docs/bugs/opencode-*) legacy_rel="legacy/$relpath" ;;
        .agents/shared/planning/opencode-native-tools/*) legacy_rel="legacy/$relpath" ;;
        example/SKILL.md) legacy_rel="legacy/example/SKILL.md" ;;
        *) continue ;;
    esac
    leg_sha="$(awk -v r="$legacy_rel" '$2==r {print $1; exit}' \
        .agents/shared/planning/agent-base-v2/evidence/phaseA/legacy-build/legacy-tree.manifest.txt)"
    if [ "$src_sha" = "$leg_sha" ]; then
        echo "OK  $relpath" >> .agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/single-files.diff.txt
    else
        echo "DIFF $relpath  src=$src_sha  leg=$leg_sha" >> .agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/single-files.diff.txt
    fi
done < "$SINGLE"

# Emit pre-archive-map.md as the final before/after artifact (both roles + boundary-exceptions status)
cat > .agents/shared/planning/agent-base-v2/evidence/phaseA/pre-archive-map.md <<'EOF'
# BEFORE/AFTER MAP — Phase A final artifact (FINAL — addendum-resolved)

This file is the frozen before/after artifact documenting WHAT EXISTS WHERE for the opencode stack removal.

## Scope sources
- Source: `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP"
- Authoritative classification: exploration addendum (2026-10-13).

## Terminal states observed

| State | Row count | Notes |
|-------|-----------|-------|
| archived-to-legacy | 11 (STACK-1..11) + 5 (BASE-1..5) = 16 | Whole files/dirs removed from live source; mirror shape under legacy/. |
| edited-in-place | 26 (TOUCH-1..26) | Pre-edit byte content preserved by git history (primary) + optional pre-edit-snapshots/<file>.before.txt (secondary). NOT archived whole. |
| retained-live | 0 (EMPTY) | No live dependency on the opencode stack (D-A7 verdict). `boundary-exceptions/` directory created empty as schema future-proofing. |

## Move set (archived-to-legacy) — final 11 STACK + 5 BASE rows
[... see `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP" tables ...]

## Touch set (edited-in-place) — 26 rows
[... see `phaseA-legacy-archive.md` §"BEFORE/AFTER MAP" Touch set table ...]

## LEAVE ALONE — final
- agents/coder/ (opencode-free by design)
- daemon/services/keyword_extraction.py + daemon/services/context_injection.py (live-shared infra; comment rephrases only)
- docs/mcp-kb-usage.md (upstream product, D6)
- .agents/*/RESULTS/ + .agents/*/LESSONS/
- data/, data_dev/ DB files (stray `opencode_sessions.db` at repo root: record-and-do-not-delete; see `evidence/phaseA/stray-db-artifacts.txt`)
- .inspiration-projects/opencode_skill_src/ (gitignored)
EOF
```

Per-step verification:
- `for name in approver developer planner reviewer tidier; do wc -c .agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/agents-$name.diff.txt; done` — all must print `0`.
- Each opencode dir diff must be empty.
- All 8 per-module `diffs/opencode-modules/<module>.diff.txt` files must contain `OK ` (no `DIFF `).
- `single-files.diff.txt` must start with `OK ` for every archived single file and end with NO `DIFF ` lines.
- `evidence/phaseA/pre-archive-map.md` exists; documents the FINAL state with zero `retained-live` rows.

### Step 9 — D-A1 followup: legacy/ gitignore status record

```bash
# Check whether legacy/ is gitignored already; record the decision basis.
if grep -E '^legacy/?' .gitignore; then
    echo "legacy/ already in .gitignore" > .agents/shared/planning/agent-base-v2/evidence/phaseA/gitignore-status.txt
else
    echo "legacy/ NOT in .gitignore — recommend adding (mirror backup/ pattern; backup/ is git-tracked so legacy/ SHOULD be too)" \
        > .agents/shared/planning/agent-base-v2/evidence/phaseA/gitignore-status.txt
fi
# This PR DOES NOT mutate .gitignore (out of scope for this phase); the recommendation
# is recorded for the mission PR author to handle if desired.
```

### Step 10 — Self-heal (NOT used in this phase; reference only)

This phase does no live-system writes. If a diff step fails, retry once after re-reading the failing step; on second failure, record the diff in `.agents/shared/planning/agent-base-v2/evidence/phaseA/diffs/FAILURE-<step>.txt` and STOP — the executor should NOT silently swallow mismatches.

## Per-Step Verification Summary

| Step | What to check | Pass threshold |
|------|---------------|----------------|
| 1 | Branch = `feature/agent-base-v2`, HEAD = `885a19d4f45a3403a1bc615ec0f393630c938c84`, porcelain empty; FAKE_HOME created | exact text match + empty porcelain |
| 2 | `d-a2-resolution.txt` contains `ARCHIVE_EXAMPLE_SKILL=1` (FINAL — addendum-resolved); `boundary-exceptions/.gitkeep` + `README.md` exist; `ordering-constraint.txt` exists; `anomalies.txt` empty | exact value `1`; schema files exist |
| 3 | 5 base dir manifests + 8 `daemon/opencode/*.py` per-module manifests (`source-trees/opencode-modules/` subdir) + 4 move-set dir manifests + 1 single-file manifest exist (non-empty); `single-files.manifest.txt` lists 16 entries (STACK-2, STACK-3 [skill.md + .bak], STACK-5..7, STACK-9..10, STACK-8 × 7 planning files, STACK-11 unconditional); each has `.sha256`; `stray-db-artifacts.txt` exists | non-empty + entry count match; per-module granularity enables per-role verification |
| 3b | `pre-archive-map.md` (`evidence/phaseA/pre-archive-map.md`) emitted as the FINAL before/after artifact with both roles in Notes; `boundary-exceptions/README.md` documents the empty `retained-live` state (D-A7 verdict); `anomalies.txt` empty (FINAL verdict holds) | exists, non-empty, both roles named, retained-live = 0 |
| 4 | `find legacy -type d` lists ≥10 directories (added `legacy/example` for the unconditional D-A2 archive); all top-level mirror dirs present per BEFORE/AFTER MAP | exact dir count + match |
| 5 | All 5 base ↔ legacy base diff-r are clean | zero diff lines |
| 6 | All 11 archived pairs (STACK-1..11 + example/SKILL.md unconditional + the per-dir entry) diff clean; uses `cp -p`/`cp -pR` (D-A4); uses FINAL, unconditional D-A2 example/SKLL.md | zero diff lines |
| 7 | `legacy-tree.manifest.txt` + `legacy-tree.sha256` (64 hex) + `legacy-tree.by-stream.md` partition populated with role-tagged sections (protocol/comm | session/API | shared | tests | planning | bug-doc | ported-contract-spec | retained-live=EMPTY) | non-empty + hex + correct partition |
| 8 | All per-dir diffs empty + per-module module-manifest diffs (8 modules) all `OK ` (no `DIFF `) + single-files diff contains no `DIFF ` lines | empty + `OK` count = expected total (16 + 5 base + 4 dir + 8 module = ~33 `OK` lines across all diff files) |
| 9 | gitignore-status.txt exists; non-empty | non-empty |

## Phase Rollback (git-level)

- This phase's net diff = many new files inside `legacy/` + zero changes outside `legacy/` + zero mutations under `agents/*`.
- **Rollback command** (executed in worktree on `feature/agent-base-v2`):
  ```bash
  # Soft rollback (recommended): leave legacy/ on disk so future re-execute can compare
  git restore --staged legacy/ evidence/ 2>/dev/null || true
  git checkout -- legacy/ evidence/ 2>/dev/null || true
  git clean -fd legacy/ evidence/    # removes untracked content too
  # Verify
  find legacy -type f 2>/dev/null | wc -l    # must be 0
  git status --porcelain | grep -E '^(legacy|evidence/)' || echo "clean"
  ```
- Hard rollback if legacy/ ever needs to disappear entirely: `rm -rf legacy/` then `git clean -fdx legacy/` (verify with `ls legacy`).
- No other repo state changes means no other rollback needed — the live tree is byte-identical pre-archive.

### P6 mirror — reverse-direction counterpart (architect enrichment)

> **P6 mirror (architect-verified 2026-10-13):** the plan's "A last/skippable" note covers the forward direction; the **reverse** direction is the load-bearing counterpart. Reverting Phase A **alone** (with Phase B and/or Phase C landed) leaves broken states:
>
> - If B and C are landed and A is reverted: `legacy/` is gone, but the live tree no longer contains the opencode-era base content or the opencode stack — fine in steady state. **However**, if C is also reverted in the same operation (the typical "undo the whole mission" pattern), the **opencode stack code is back in `daemon/opencode/` and `daemon/tools/external_opencode.py`** but the live tree may no longer match what `legacy/` would have provided. The executor's mental model: A is the **additive safety net** — its loss is OK if everything else stays consistent; A's loss is **NOT** OK if any other phase's rollback assumes A's contents still exist.
> - More specifically: **reverting A alone orphans B/C** — Phase B's per-pair rollback command (see `phaseB-base-copy.md` §"Phase Rollback") copies from `legacy/agents/<name>/` to restore base content; if A is gone, B cannot be rolled back. Phase C's wiring-excision rollback may also rely on `legacy/` as a reference for what opencode code looked like.
> - **Mitigation** (recorded, not blocking): when reversing the mission, revert in order **C → B → A** (whole-mission reverse). Do NOT revert A in isolation when B and/or C are live. If only A must be reverted (rare), first revert C and B, then revert A.
> - **Whole-C-only reverse (companion rule from architecture-recommendation P6):** Phase C reverts as a **unit** (C.1↔C.2 are mutually broken in partial states: wiring-without-module = import-dead-code; module-gone-with-wiring = `ImportError` at `daemon/manager.py:78`). The C-revert unit then permits the B and A reverts in the standard C → B → A order.

## Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | FAKE_HOME fence not honored — script touches $HOME-derived install dir → live install deletion (incident 2026-10-12) | **Critical** (data loss) | Low (since fence is documented + bound to `mktemp`) | Step 1 sets `FAKE_HOME` to `mktemp -d -t agent-base-v2-XXXXXX`, exports it, and adds `trap`; executor must verify the printed FAKE_HOME path before continuing. Any script under this phase MUST use $FAKE_HOME or worktree-anchored absolute paths; live-shape resolution is forbidden by Step 1's grep guard. |
| 2 | Per-file sha256 mismatch between source and legacy — partial copy / permission loss / dotfile missed | High (defeats archive as safety net) | Low (cp -R + diff -r verification) | Step 5 + Step 6 use `diff -r` for every archived pair as a hard gate; Step 8 adds sha256-level proof on top. Any `DIFF ` line in `evidence/phaseA/diffs/*.diff.txt` blocks phase completion. |
| 3 | Mixing hash tools (e.g., `shasum -a 256` + `sha256sum`) produces format-incompatible evidence, blocking Phase E | Medium (evidence pack coherence) | Medium | Step 3 enforces `shasum -a 256` per D-A3 and matches lib.sh:1928/1944 family; warning embedded in step prose. |
| 4 | `legacy/` accidentally excluded from releases via stage.sh enumeration | Medium | Low | stage.sh copies only enumerated trees (agents, frontend, plugins, + binary, config, launcher.sh) — `legacy/` is not enumerated → natively excluded (verified at stage.sh:397 line family). Recorded in D2 justification; no change needed. |
| 5 | `.bak` file missed during `cp -R` (mismatch between glob and `find .` with dotfile handling) | Low (single file) | Low | `_prompt_system/innate-skills/opencode/.bak` is copied via `cp -R agents/_prompt_system/innate-skills/opencode/. legacy/...` — directory-copy handles dotfiles; `find -type f` enumerates them; verified by per-file manifest including `skill.md.bak`. |
| 6 | Future-proofing: drift between legacy/ contents and what user expects to find | Low | Medium | Phase E verification evidence pack re-hashes legacy/ tree at mission end; `legacy-tree.sha256` recorded alongside per-file manifest in evidence/phaseA/legacy-build/. |
| 7 | BASE dir content was already drifted from canonical opencode-era source (e.g., local edits between branch-cut and now) | Medium (Phase A captures current state — that's still the safety net) | Low | Phase A archives the on-disk state at execution time, which IS the safety net the mission needs. If pre-mission canonical sourcing was required, that's out of scope; this phase does not claim canonical opencode-era provenance — it claims byte-fidelity to the current branch HEAD. |
| 8 | example/SKILL.md (D-A2) wrongly classified — opencode-related but flagged as generic | Low (one file) | Low | Step 2 preflight greps the file; decision recorded in `d-a2-resolution.txt`; audit-able by Phase E reviewer. |

## Definition of Done

Phase A is DONE when **ALL** of the following are true (each checkable by an automated shell snippet or by `ls`/`cat`):

- [ ] **DoD-A1**: `legacy/` exists at worktree root; the BEFORE/AFTER MAP is the source of truth for the expected layout. `find legacy -mindepth 1 -maxdepth 1 | sort` (W1 fix: top-level only — NOT recursive, which would otherwise return ~88 entries) lists **EXACTLY 7 top-level entries** = `agents/`, `daemon/`, `tests/`, `test/`, `docs/`, `example/`, `.agents/` (the 7 mirrored roots from STACK-1..11 + BASE-1..5). Any deviation is an EXECUTION ANOMALY (halt + engage user per executing-extension protocol).
- [ ] **DoD-A2**: per-file byte-diff (Step 5 + Step 6 `diff -r` and per-file checks) is CLEAN for ALL of:
  - 5 base agent dirs (approver, developer, planner, reviewer, tidier) — per-pair `diff -r`
  - `daemon/opencode/` (whole dir, both roles) — `diff -r`
  - `daemon/tools/external_opencode.py` — file diff
  - `agents/_prompt_system/innate-skills/opencode/` (both files including `.bak`) — `diff -r`
  - `tests/opencode/` (all 12 files including `__init__.py` + `conftest.py`) — `diff -r`
  - `test/packs/opencode_native_tools_unit_test.sh`, `test/packs/ab/top_opencode_unit_test.sh`, `test/packs/regression_opencode_e2e_test.sh` — file diffs
  - `.agents/shared/planning/opencode-native-tools/` (all 7 files) — `diff -r`
  - `docs/bugs/opencode-session-manager-memory-leak.md`, `docs/bugs/external-opencode-wait-latency.md` — file diffs
  - `example/SKILL.md` (D-A2 FINAL-resolved: INCLUDED) — file diff
- [ ] **DoD-A3**: sha256 evidence manifests exist in `.agents/shared/planning/agent-base-v2/evidence/phaseA/`:
  - `source-trees/agents-{name}.manifest.txt` × 5 (each + .sha256)
  - `source-trees/daemon-opencode.manifest.txt` (+ .sha256)
  - `source-trees/tests-opencode.manifest.txt` (+ .sha256)
  - `source-trees/agents-_prompt_system-innate-skills-opencode.manifest.txt` (+ .sha256)
  - `source-trees/opencode-modules/{__init__.py,client.py,constants.py,registry.py,repository.py,server.py,session_manager.py,state.py}.manifest.txt` × 8 (per-module granularity enables per-stream verification of the two roles)
  - `source-trees/single-files.manifest.txt` (+ .sha256; **16 lines** = STACK-2 external_opencode.py + STACK-3 skill.md + STACK-3 .bak + STACK-5..7 (3 test pack files) + STACK-9..10 (2 bug doc files) + STACK-8 (7 planning .md files) + STACK-11 example/SKILL.md unconditional = 1+2+3+2+7+1 = 16; STACK-11 included unconditionally per D-A2 addendum-resolved. S1 fix: prior draft said 15; the correct count is 16.)
  - `legacy-build/legacy-tree.manifest.txt` (+ `legacy-tree.sha256`)
  - `legacy-build/legacy-tree.by-stream.md` — legacy tree partitioned by the two roles (protocol/comm | session/API | shared/infrastructure | base-content | planning | bug-doc | ported-contract-spec); enables verification that each role's archive is complete independently
  - `diffs/agents-{name}.diff.txt` × 5 (all zero bytes)
  - `diffs/daemon-opencode.diff.txt`, `diffs/tests-opencode.diff.txt`, `diffs/agents-_prompt_system-innate-skills-opencode.diff.txt` (all zero bytes)
  - `diffs/opencode-modules/<module>.diff.txt` × 8 (all zero bytes — per-module per-role verification)
  - `diffs/single-files.diff.txt` (no `DIFF ` lines; only `OK` lines for each archived single file)
  - `d-a2-resolution.txt` (`ARCHIVE_EXAMPLE_SKILL=1` per D-A2 addendum-resolved; the Step 2 preflight records this unconditionally)
  - `gitignore-status.txt`
  - `stray-db-artifacts.txt` — records any repo-root `opencode_sessions.db`/WAL/SHM file present at execution time (zero, one, or several entries). Does NOT delete any such file (per addendum note).
  - `boundary-exceptions/` directory exists (empty per D-A7) — schema placeholder retained for future-proofing; any non-empty entry indicates a live-used surface was found and flagged for user decision.
  - `anomalies.txt` — empty (no unexpected opencode-tied files surfaced at execution time per executing-extension protocol).
  - `pre-archive-map.md` — the final BEFORE/AFTER MAP emitted as a frozen artifact (records every row, both roles in notes, the D-A6 verdict that the map is final).
- [ ] **DoD-A4**: legacy/ tree sha256 computed via `shasum -a 256 <manifest>` matches `legacy-tree.sha256` (this is the digest whose value the verification pass claims).
- [ ] **DoD-A5**: NO files in live source tree (`daemon/opencode/`, `agents/_prompt_system/innate-skills/opencode/`, `tests/opencode/`, `test/packs/*opencode*`, `docs/bugs/opencode-*`, `example/SKILL.md`, `daemon/tools/external_opencode.py`) have been modified — verified by `git status --porcelain` restricted to those paths returning empty. *(Phase A does NOT delete; Phase C deletes — Phase A only archives.)*
- [ ] **DoD-A6**: NO mutations under `agents/*[v2]/*` (binding D4) — `git status --porcelain -- 'agents/*[v2]*'` empty.
- [ ] **DoD-A7**: NO live system contact — no daemon restart, no DB writes, no promote, no install-dir writes. Verified by absence of any non-worktree process activity in the executor's session log.
- [ ] **DoD-A8**: No evidence-file produced this phase is missing or partial; the executor can produce an `evidence/phaseA/MANIFEST.txt` listing every expected file (auto-emit by `find .agents/shared/planning/agent-base-v2/evidence/phaseA -type f | sort`).
- [ ] **DoD-A9** (D-A7 verdict-gate pass): `evidence/phaseA/boundary-exceptions/` exists and is empty — confirming the FINAL verdict that no surface requires `retained-live` status. Any non-empty entry triggers the user-decision workflow; Phase A does NOT silently invent a retained-live row.
- [ ] **DoD-A10** (D-A8 ordering-constraint evidence): `evidence/phaseA/ordering-constraint.txt` records a one-line assertion that Phase C wiring excision does NOT execute until Phase B has retired the 5 base `meta.json` files. (Phase A does not enforce this constraint — it documents it for Phase C executor visibility.)

## P5 — Informational note on release manifest scope (architect enrichment)

> **P5 (architect-verified 2026-10-13):** the release manifest is **content-only** (`scripts/upgrade/lib.sh:1928` `_tree_manifest` walks `find . -type f | sort` and emits `<sha256>  <relpath>` lines — no modes, no mtimes, no symlink resolution). Phase A's archive uses `cp -pR` which preserves modes/mtimes, but **nothing in the manifest verifies them**. Equivalently: **"content identity ≠ proven-by-modes"** — a future maintainer reading the manifest must not over-trust it as a mode/timestamp proof. Phase A's DoD per-file `diff -r` + sha256 verification closes the content hole; mode/mtime drift, if introduced, would be invisible to the manifest. Recorded for future-proofing. (Same note as in `phaseB-base-copy.md` §P5; folded identically in both plans for symmetry.)

## Cross-Phase Hand-off

- Phase B (immediately following this phase) will:
  - Read `.agents/shared/planning/agent-base-v2/evidence/phaseA/` only for cross-checks (e.g., confirming legacy/ is complete before it relies on it as a base-content rollback source).
  - Capture ITS OWN v2 before-snapshot manifests (D4 ZERO-TOUCH proof).
- Phase C (after Phase B) will use `legacy/` as the canonical reference for what opencode code LOOKED LIKE (helps phase C confirm no string-removed-during-wiring is wrong).
- Phase D uses `legacy/` for doc cross-reference updates (Phase D will add referential notes pointing INTO legacy/ where docs referenced the removed files).
- Phase E (verification evidence pack) will read `evidence/phaseA/diffs/*.diff.txt` and re-run `diff -r` against `legacy/` to confirm leg A's verification is reproducible after Phase B/C/D execute.

## Open Questions (status: all RESOLVED via exploration addendum)

1. **`example/SKILL.md` classification** — RESOLVED (D-A2 addendum): INCLUDED. `legacy/example/SKILL.md`. The Step 2 preflight still runs the grep but resolves `ARCHIVE_EXAMPLE_SKILL=1` unconditionally per the addendum verdict.
2. **`legacy/` gitignore decision** — **S6 RESOLVED (R10 §11.4 #3 boundary case)**: `.gitignore` opencode lines (notably the `opencode_sessions.db` ignore row) are **INTENTIONAL-KEEP** — they remain functional for D7 orphan-DB protection post-migration (the ignore prevents the orphan `opencode_sessions.db` from polluting `git status` after the stack is removed). Allowlisted with justification: keeps D7 intact. The Phase A `legacy/` directory itself is a different question — tracked (mirroring `backup/` precedent) per D2 justification. The optionality marker is dropped; the executor does NOT need to make a fresh decision.
3. **D-A1 (planning-docs placement)** — RESOLVED (addendum-locked): `legacy/.agents/shared/planning/opencode-native-tools/` (mirror source shape; planning/archive convention considered and rejected). FINAL.
4. **Live-dependency verdict** — RESOLVED (D-A7 addendum): EMPTY `retained-live` state per Verdict 2; coder is opencode-free by design; no live callers outside the 5 retiring base agents (which Phase B retires); manager startup-recovery DISABLED. The `boundary-exceptions/` directory is created empty as schema future-proofing.
5. **Stack classification** — RESOLVED (D-A6 addendum): ONE retirement unit (`daemon/opencode/`), TWO named roles (protocol/comm dispatch in `server.py` + session/API lifecycle across the other 7 modules). Executing-extension protocol governs any unexpected file at execute time (HALT + engage user).
6. **Ordering constraint** — RESOLVED (D-A8 addendum): stack removal MUST land WITH or AFTER Phase B; Phase A is safe anytime. Phase A documents this constraint in `evidence/phaseA/ordering-constraint.txt` for Phase C executor visibility.
7. **Repo-root stray DB artifact** — RESOLVED (addendum-noted): record-and-do-not-delete if present at execute time. The file becomes inert post-Phase-C because boot-recreate dies with the code. Mission report mentions for visibility.
