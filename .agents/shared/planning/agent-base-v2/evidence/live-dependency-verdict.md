# LIVE-DEPENDENCY VERDICT — agent-base-v2 mission (Step 4.5)

**Date:** 2026-10-13 · **Executor:** Phase E · **Scope:** ENTIRE opencode stack, single package (addendum VERDICT 1: session/API role + comm-role layer inside).

**RESOLUTION (addendum VERDICT 2, relayed 2026-10-13): NO surface requires isolation → FULL STACK REMOVAL, no boundary-rule exception. `boundary_rule_exceptions: []`. `retained_live_surfaces_in_opencode_stack: []` — the `retained-live, USER DECISION ITEM` terminal state is EMPTY for this mission. No USER DECISION ITEM arises from this verdict.**

Sources: (primary) leader exploration addendum VERDICT 1+2 (received 2026-10-13); (secondary) Phase E executor grep re-verification 2026-10-13T15:5xZ — all patterns below re-run at exec time, all returned EMPTY as expected (raw output preserved at `/tmp/ens-phaseE/step45-greps.txt`; summary baked into the table).

## Per-surface verdict table

| Surface | Stack role | Verdict | Executor re-verification (exec-time) | Phase C branch | Boundary flag | UDI |
|---|---|---|---|---|---|---|
| `daemon/opencode/` (8 modules) | session/API role | **dead** | `grep -rn "from daemon.opencode|daemon.opencode" --include=*.py daemon/` → 0 hits; zero mounts across `daemon/api.py` + `daemon/routers/` (stale `__init__.py:13` claim verified FALSE) | full-stack | none | none |
| `daemon/tools/external_opencode.py` | SOLE consumer (comm role) | **dead** | `grep -rn "daemon.tools.external_opencode" --include=*.py --exclude-dir=legacy .` → 0 hits | full-stack | none | none |
| `daemon/opencode/__init__.py` | shared module | **dead** | file absent from active tree; archived at `legacy/daemon/opencode/__init__.py` (Step 4 manifest) | full-stack | none | none |
| `daemon/manager.py` opencode blocks | InstanceManager-coordinated | **dead** | `grep -n "opencode" daemon/manager.py` → 0 hits | full-stack (edited-in-place) | none | none |
| `daemon/tools/instance.py` | spawn wiring | **dead** | grep → 0 hits | full-stack (edited-in-place) | none | none |
| `daemon/tools/_tool_registry.py` | tool registrations | **dead** | grep → 0 hits | full-stack (edited-in-place) | none | none |
| `daemon/config.py:168`, Makefile, pyproject.toml, .env.example | TOUCH surfaces | **dead** | grep → 0 hits each | full-stack (edited-in-place) | none | none |
| `agents/_prompt_system/innate-skills/opencode/skill.md` (+`.bak`) | agent-facing innate skill | **dead** | active dir ABSENT; archive copy verified at `legacy/agents/_prompt_system/innate-skills/opencode/` (2 files) | full-stack | none | none |
| `agents/coder/` | NOT opencode stack | **opencode-free BY DESIGN** | contamination guards at `tests/unit/test_coder_agent.py` are a test-wall invariant (Step 8 asserts they pass); grep of `agents/coder/` metas → 0 opencode | n/a (stays live) | none | none |
| 5 retiring base agents' `innate_skills: ["opencode"]` | agent meta | **dead** | `find agents -maxdepth 2 -name meta.json \| xargs grep -il opencode` → 0 matches (post-Phase-B base = v2 content) | full-stack (via v2 copy) | none | none |
| `tests/opencode/` (12 files) | tests | **dead** | archived `legacy/tests/opencode/` (12 files, Step 4 manifest) | full-stack (archived) | none | none |
| 3 opencode test packs | test packs | **dead** | archived `legacy/test/packs/` (3 files, Step 4 manifest) | full-stack (archived) | none | none |
| 7 opencode-native-tools planning docs | planning records | **dead** | archived `legacy/.agents/shared/planning/opencode-native-tools/` (7 files) | full-stack (archived) | none | none |
| 2 opencode bug docs | bug records | **dead** | archived `legacy/docs/bugs/` (2 files) | full-stack (archived) | none | none |
| `example/SKILL.md` | example artifact | **dead** | archived `legacy/example/SKILL.md` | full-stack (archived) | none | none |
| `daemon/services/keyword_extraction.py` + `context_injection.py` | NOT opencode stack (D5 LIVE-SHARED) | **intentionally kept live** | both present; live callers attested: `attestation_report_judge.py:35-328` (5 refs), `daemon/graph.py:10794`, `plugins/opendesign/generate.py:650` | n/a (stays live) | none | none |
| MCP configs wiring opencode | MCP | **N/A — zero surfaces** | `grep -rin opencode daemon/mcp/ config.yaml` → 0 hits | n/a (stream empty) | none | none |
| `docs/mcp-kb-usage.md` | D6 | **intentionally kept (D6 false positive)** | allowlisted (r10-allowlist.md D6 rows; strictly-upstream verification on record) | n/a (D6) | none | none |
| `~/.opencode_skill` + `~/.opencode` runtime | runtime | **dead** | no caller (sole consumer removed); port 4095 not part of any live wiring | n/a (runtime) | none | none |
| `data/`, `data_dev/` DBs (opencode_sessions table) | runtime | **dead / boot-recreate dies with code** | orphan DB artifact class (D7) — note, not defect | n/a (DBs) | none | none |
| `.inspiration-projects/opencode_skill_src/` | gitignored | **not in active codebase** | gitignored; outside tracked tree | n/a | none | none |

## DoD check for this artifact

- Every addendum MOVE/TOUCH/LEAVE-ALONE surface has a row ✅
- Every Verdict is one of `dead` / `intentionally kept live` / `N/A — zero surfaces` / `opencode-free BY DESIGN` ✅
- `boundary_rule_exceptions` empty (boundary rule considered, NOT invoked) ✅
- Coder row present + resolved ✅
- MCP finding recorded ("NO MCP configs anywhere reference opencode") ✅
- Ordering constraint recorded (removal lands WITH/AFTER Phase B, never before — verified in commit graph: 97f3ad43c B precedes 2e3bf2b2a C/1..C/4) ✅
