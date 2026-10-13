# BEFORE/AFTER MAP — agent-base-v2 mission (whole-mission; FULL STACK; three terminal states)

**Date:** 2026-10-13 · **Scope:** ENTIRE opencode stack as ONE package (addendum VERDICT 1) + the agents/ base-copy stream. Terminal states: **(archived-to-legacy)** / **(edited-in-place, git-preserved)** / **(retained-live, USER DECISION ITEM — EXPECTED EMPTY)**.

**R8 capture-before-boot verification note (binding):** the base==v2 equality proof (`evidence/base-vs-v2-diffs/<name>.diff.txt`, files written 15:55Z) and the v2 pre/post manifests (15:54Z) and the legacy re-verify (15:56Z) were ALL captured BEFORE the boot-scan (`evidence/boot-scan/dev-lane-boot.log`, first write 15:58:33Z). No boot ran prior to any equality capture in Phase E; `inner_soul` runtime `memory.md` mutation therefore could not contaminate the proofs. Both checks (R8 equality + R10 committed-tree gate) stand independently (R10 §11.7).

## Stream (i) — agents/ base copy (Phase B)

| Pre | Post | Terminal state | Evidence |
|---|---|---|---|
| `agents/{approver,developer,planner,reviewer,tidier}` (opencode-era base) | overwritten with v2 byte-copy; declares NO `opencode` innate skill | (edited-in-place, git-preserved — pre-edit at `git show 885a19d4f:agents/<name>/…`) | `evidence/base-vs-v2-diffs/<name>.diff.txt` (all EMPTY) |
| `agents/<name>[v2]` ×5 | unchanged (zero-touch) | kept live (the versioned lane persists) | `evidence/pre-mission-v2-manifests/` == `evidence/post-mission-v2-manifests/` (byte-equal ×5; digests identical) |

## Stream (ii) — opencode stack MOVE → legacy/ (archived-to-legacy)

| Pre-mission path | Stack role | Post-mission path | Manifest evidence |
|---|---|---|---|
| `daemon/opencode/` (8 modules) | session/API role + comm-role layer | `legacy/daemon/opencode/` | `evidence/legacy-reverify/legacy-tree.reverify.manifest.txt` (68 lines, diff vs Phase A EMPTY) |
| `daemon/tools/external_opencode.py` | SOLE consumer (comm role) | `legacy/daemon/tools/external_opencode.py` | (same) |
| `agents/_prompt_system/innate-skills/opencode/` (skill.md + .bak) | agent-facing innate skill | `legacy/agents/_prompt_system/innate-skills/opencode/` | (same; active dir ABSENT — verified Step 4.5) |
| `tests/opencode/` (12 files) | opencode tests | `legacy/tests/opencode/` (uniform single tree, O2) | (same) |
| `test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}` | test packs | `legacy/test/packs/…` | (same) |
| `.agents/shared/planning/opencode-native-tools/` (7 docs) | planning records | `legacy/.agents/shared/planning/opencode-native-tools/` | (same) |
| `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` | bug records | `legacy/docs/bugs/…` | (same) |
| `example/SKILL.md` | example artifact | `legacy/example/SKILL.md` | (same) |
| opencode-era base agent dirs (5) | retiring base | `legacy/agents/<name>/…` (32 files) | (same) |

## Stream (iii) — TOUCH (edited-in-place, git-preserved)

| Path | Change | Pre-edit evidence |
|---|---|---|
| `daemon/manager.py` (opencode init/coordination/recovery/cleanup blocks) | blocks removed; `grep -n opencode` → 0 | `git show 885a19d4f:daemon/manager.py` |
| `daemon/tools/instance.py` (spawn wiring) | removed; grep → 0 | git history |
| `daemon/tools/_tool_registry.py` (registrations) | removed; grep → 0 | git history |
| `daemon/config.py:168`, `Makefile`, `pyproject.toml`, `.env.example` | rephrased/removed; grep → 0 | git history |
| comment rephrases (`keyword_extraction.py`, `context_injection.py`, `context_messages.py`, `critical_note_gate.py`, `waiting_children_watchdog.py`, `loader.py`, `task/repository.py`) | D5 LIVE-SHARED modules kept; opencode WORDING rephrased | git history |
| cosmetic one-liners (`agents/explorer/soul.md`, `agents/devops/soul.md`, `agents/_prompt_system/knowledge.md`) | rephrased | git history |
| tests: `test_innate_skills_refactoring.py` (P1), `test_tool_filter.py` (R1), `test_jsonb_migration.py` (R2), `test_coder_agent.py:40-43` (guards KEEP passing), `test_wanderer_agent.py` (P2), 3 mock-strips, `tests/job_queue/conftest.py:65`, P7 stragglers | rephrased post-B | git history |
| docs refresh (`docs/agents.md`, `docs/skill-evolution.md`, `testing-guide.md`, `docs/mcp-integration.md`, `docs/agent-prompt-writing-guide.md`, README/AGENTS no-op) | Phase D | git history + `evidence/docs-consistency-sweep.md` |

## Stream (iv) — retained-live, USER DECISION ITEM — **EXPECTED EMPTY (VERIFIED EMPTY)**

`retained_live_surfaces_in_opencode_stack: []` · `boundary_rule_exceptions: []` (see `evidence/live-dependency-verdict.md`). Executor verification found NO new live caller; the boundary rule was considered and NOT invoked. No USER DECISION ITEM arises from this map.

## Stream (v) — MCP configs — **EMPTY (verified)**

No MCP config anywhere references opencode: `daemon/mcp/` 0 hits, `config.yaml` 0 hits (executor grep Step 4.5 + Surface-5 audit: only Tier-5 transcript JSONs / vendored `copy_freely` manifests / backup / `.slim` — none are MCP configs).

## LEAVE ALONE (not in the map)

`agents/coder/` (opencode-free BY DESIGN; guards pass — DoD #13) · `keyword_extraction.py` + `context_injection.py` (D5 LIVE-SHARED, callers attested) · `docs/mcp-kb-usage.md` (D6) · `.agents/*/RESULTS|LESSONS` (Tier-5) · `data/`, `data_dev/` DBs (D7 orphan class) · `.inspiration-projects/opencode_skill_src/` (gitignored).

## Ordering constraint (DoD #12)

Commit graph: `495068d8b` planning → `97f3ad43c` **B** (base copy FIRST) → `2e3bf2b2a` C/1 → `7b0a102c3` C/2 (stack removal WITH/AFTER B, never before) → `300ac0ba4` C/3 → `ca222f99f` C/4 → `ba279f813` C/5 → `8cefcecf3` D. B precedes the opencode-stack move ✓ (innate-skill loader constraint honored).
