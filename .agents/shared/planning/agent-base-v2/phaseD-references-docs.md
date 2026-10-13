# Phase D — References + Docs Refresh (agent-base-v2 migration)

Owner scope: **Phase D** of the agent-base-v2 mission. Touches ONLY user-facing documentation that names agents, the opencode stack, or the v1 → v2 versioning story. Does NOT edit code, tests, the agents tree, the legacy archive, or the frontend. Siblings: `phaseA-legacy-archive.md`, `phaseB-base-copy.md`, `phaseC-opencode-excision.md` (parallel workers; this plan references them by filename and never duplicates their content).

**Controlling scope (final, 2026-10-13 + exploration addendum relay + R10 binding gate from architecture-recommendation.md):** the unit of work is the **ENTIRE opencode stack** as a single package with two named roles (session/API role + comm role layer inside). Phase D's docs sweep covers the FULL STACK surface uniformly: comm role, session/API role, shared modules in `daemon/opencode/`, InstanceManager-coordinated resources, MCP configs, agent-facing surfaces (innate skill + agent meta). Same D6/D5/Tier-5 exclusions apply. Addendum landed 2026-10-13; this plan finalizes with it incorporated. **R10 (binding user directive, 2026-10-13, architecture-recommendation.md §11) SUPERSEDES the prior 4-bucket classification for active-tree doc rows**: only the explicit allowlist (§11.3 exclusion set + P4 stale-HISTORY rows justified in the allowlist) survives as "leave"; every active-tree doc row is a mandatory FIX.

## 1. Objective

Refresh user-facing documentation to match the post-migration state (Phase A archived the opencode-era base + the ENTIRE opencode stack to `legacy/`; Phase B made `agents/<base>/` a byte-identical copy of `agents/<name>[v2]/` for the five versioned agents; Phase C excised opencode wiring from daemon code). Specifically:

1. Remove every stale `opencode` claim that describes **any of our retired stack components** from user-facing docs. The docs sweep covers the FULL STACK uniformly: the opencode comm role (protocol layer), the opencode session/API role (single package), shared modules in `daemon/opencode/`, InstanceManager-coordinated resources, MCP configs (per addendum: NO MCP configs anywhere reference opencode — verified zero), and agent-facing surfaces (`opencode` innate skill + `innate_skills: ["opencode", ...]` in agent meta). Each hit is classified and treated the same way.
2. Update `meta.json` and `soul.md`/`rule.md` examples so they reflect what the v2 agents actually look like (innate_skills `["todo", "chart", "dynamic-skill"]`, no opencode).
3. Refresh `README.md` and `AGENTS.md` references (conditional — only if the docs-consistency sweep finds stale inline claims; line ranges 84, 104–107, 183 in `README.md` and 123 in `AGENTS.md` are the dispatch-cited anchors, grep-verify at exec time).
4. Refresh `docs/agent-prompt-writing-guide.md §9` so the "v1 → v2 Migration" narrative reflects the new base := copy-of-v2 reality.
5. Add a CHANGELOG `[Unreleased]` entry under the no-bump convention (record `0f328337` — `[Unreleased]` entries stay until a bump deletes/pointer-annotates them; this mission does NOT bump).
6. Run a docs-consistency sweep that classifies every remaining `opencode` hit in `docs/`, `README.md`, `AGENTS.md` into **stale→fix** / **false-positive→leave** / **historical→leave** / **intentional→leave** and produces a recorded classification table. Sweep patterns cover the FULL STACK; sweep output is the input to Phase E's repo-wide residual-references audit.
7. Document `docs/mcp-kb-usage.md` as a verified false positive (the file documents the upstream `sst/opencode` product, not our service) — UNTOUCHED.
8. Document `docs/test-suite-known-issues.md:43` as a historical→leave reference (live OpenCode integration test was archived in Phase A; the test path becomes `legacy/tests/opencode/...` post-Phase-A) — UNTOUCHED in Phase D; Phase C follow-up may rewrite.

## 2. Scope

### In scope — files this phase may edit

- `docs/agents.md` (§2 table rows for `developer`, `reviewer`, `tidier`, `approver`, `planner`; §3 meta.json example; §3.4 `soul.md` example block; §3.5 `rule.md` example block; §7 innate-skills table + meta.json example)
- `docs/skill-evolution.md` (§3 developer `meta.json` example at the line containing the `["opencode", "dynamic-skill"]` snippet)
- `docs/agent-prompt-writing-guide.md` (§9 — "v1 → v2 Migration")
- **`testing-guide.md`** (B4 fix-pass 2) — repo-root test-runner guide. Two LIVE opencode hits: `:15` ("Integration tests (requires live OpenCode server)") and `:94` (the `integration` marker row "Live OpenCode server tests"). MANDATORY FIX per R10 (active testing guidance, not historical/audit). Edit both lines to use a still-current equivalent (e.g., "requires live LLM/fenced dev lane" or remove the `integration` marker's opencode reference and replace with the v2 marker landscape).
- **`docs/mcp-integration.md`** (B2 fix-pass 3) — active MCP-integration doc with 2 LIVE opencode hits: `:364` ("#### Example: OpenCode Configuration") and `:366` ("Add to your OpenCode configuration file:"). MANDATORY FIX per R10 (active doc, NOT allowlist-eligible). Rephrase both lines to use a generic MCP transport example (e.g., "Add to your MCP client configuration:") or remove the example section.
- `README.md` (only if the docs-consistency sweep finds a stale inline reference; default: structural example refreshes only — line ranges 84, 104–107, 183 are mostly structural `agent_dir`/`agents/` tree representations)
- `AGENTS.md` (only if the docs-consistency sweep finds a stale inline reference; line 123 is the "Agents live in `agents/<agent_id>/`" header — structural, likely needs an annotation only)
- `CHANGELOG.md` (new `[Unreleased]` entry under the divider)

### Out of scope — explicitly NOT touched by this phase

- `docs/mcp-kb-usage.md` — verified false positive (the upstream sst/opencode product). DO NOT edit; this phase only adds the verification note to the docs-consistency sweep table.
- `docs/test-suite-known-issues.md:43` — historical record of a live OpenCode integration test that was archived in Phase A. The reference becomes a pointer to `legacy/tests/opencode/` post-Phase-A. Annotate as historical→leave (Phase C or follow-up may rewrite).
- `docs/plans/*.md` and `docs/bugs/*.md` — historical planning/bug docs that mention opencode-era filenames. Historical→leave in the docs-consistency sweep; out of mission scope for rewriting.
- `docs/skill-evolution.md` body text beyond the meta.json example snippet — body is current behaviour for the documented agent that uses both `dynamic-skill` and an `opencode` innate skill (this is a CONFIG EXAMPLE showing the `innate_skills` + `tools.allow` pattern; both innate_skills entries are valid for an agent that uses both). Refresh only the `innate_skills` array; preserve the surrounding prose.
- `frontend/jobs.component.ts:2389` — note only (verify-safe; no edit). The frontend pin is part of the tier-1 wiring test wall in Phase E, not a Phase D doc edit.
- All `agents/**` files, `daemon/**`, `tests/**`, `legacy/**` — Phase D does NOT write to these. Phase B owns agent-dir copies; Phase C owns daemon excision; Phase A owns legacy archive.
- Worktree `main` checkout (`/home/nea/ensemble-src`) — DIRTY; never touch.

### Edit-authority boundaries

- User-facing DOCS = Phase D.
- Comment-only code sites = Phase C.
- Test pin files = Phase B (during the base copy) and Phase E (verification only).
- Frontend = verify-safe only (no edits).

## 3. Dependencies & Ordering

| Position | Phase | Produces what Phase D needs |
|---|---|---|
| Before D | **Phase B (base copy)** | The five base agent dirs exist with v2 content. Phase D's docs edits describe post-Phase-B reality, so its examples must use v2 meta.json values (`["todo", "chart", "dynamic-skill"]`, `version: "2.0.0"`), not v1/opencode values. |
| Before D | **Phase C (opencode excision)** | Daemon code is free of opencode wiring. Phase D examples must NOT show any agent with `innate_skills: ["opencode"]` since no post-Phase-C agent has it. |
| Independent of D | **Phase A (legacy archive)** | Archives opencode-era base + comm service to `legacy/`. Phase D does not depend on A's outputs but does reference A's archive path in the docs-consistency sweep annotation. |
| After D | **Phase E (verification)** | Phase E's docs-consistency sweep re-greps `docs/`, `README.md`, `AGENTS.md` after D's edits to assert no `opencode` claim about our service remains; D's sweep table is the input to E's assertion. |

D is not on the boot-scan path (Phase C owns boot proof; Phase E re-runs boot-scan with D's docs in tree).

## 4. File Inventory (exact paths)

Edits land at:

| Path | Lines cited (verify at exec time) | Nature |
|---|---|---|
| `docs/agents.md` | §2 table rows for developer/reviewer/tidier/approver/planner (≈lines 37, 39, 41, 42, 43 — grep-verify); §3 `meta.json` example (≈line 100); §3.4 `soul.md` example block (≈lines 152–165, especially 154, 159); §3.5 `rule.md` example block (≈lines 170–185, especially 178); §7 innate-skills table (≈line 416); §7 `meta.json` example block (≈lines 425–428) | Replace `opencode` claims with post-Phase-D / v2 reality. |
| `docs/skill-evolution.md` | The `developer` meta.json example block (≈line 174 with `["opencode", "dynamic-skill"]`) | Refresh `innate_skills` array to v2 pattern. Preserve the surrounding prose. |
| `docs/agent-prompt-writing-guide.md` | §9 "v1 → v2 Migration" (≈lines 234–240) | Refresh narrative from "v1 → v2 migration happened" to "base := copy of v2; v2 unchanged + still the live path" |
| `README.md` | ≈lines 84, 104–107, 183 (agent_dir payload, agents/ tree example, agents/ comment) | Conditional — only edit if docs-consistency sweep flags a stale claim. Default: add an annotation line below the tree. |
| `AGENTS.md` | ≈line 123 ("Agents live in `agents/<agent_id>/` with files:") | Conditional — annotation only. |
| `CHANGELOG.md` | Insert new bullet section AFTER the `---` under `## [Unreleased]` (≈line 10) | Add migration entry. |

Non-edits (read only):

| Path | Why read-only |
|---|---|
| `docs/mcp-kb-usage.md` | Verified false positive. Touching it is out of scope. |
| `agents/developer[v2]/meta.json`, `agents/reviewer[v2]/meta.json`, etc. | Phase D does NOT modify agent dirs. Phase B copies these into base. Phase D's docs edits DESCRIBE them; read to verify the example values match the actual v2 meta.json content (e.g., `innate_skills: ["todo", "chart", "dynamic-skill"]`). |
| `legacy/` (after Phase A) | Phase D reads `legacy/` to confirm the opencode-era base docs/scripts were moved; no edits. |

## 5. Step-by-Step Operations

### Step 0 — Read current ground truth (mandatory before any edit)

1. From the worktree root:
   ```
   git status --porcelain
   ```
   must be empty (worktree clean at 885a19d4f pre-D; worktree will be dirty once A/B/C run before D — that is expected; D runs after C).
2. Verify Phase B has produced the five base copies:
   ```
   ls agents/developer agents/reviewer agents/planner agents/tidier agents/approver
   ```
   Each must show `meta.json` and the same file list as the matching `[v2]` dir.
3. Confirm v2 `meta.json` innate_skills for at least two agents (ground-truth for the docs example):
   ```
   cat agents/developer[v2]/meta.json | grep innate_skills
   cat agents/reviewer[v2]/meta.json  | grep innate_skills
   ```
   Expected: `["todo", "chart", "dynamic-skill"]` for both.
4. Grep-verify the cited line numbers in this plan are still valid:
   ```
   grep -n "opencode" docs/agents.md
   grep -n "opencode" docs/skill-evolution.md
   grep -n "v1 → v2 Migration" docs/agent-prompt-writing-guide.md
   grep -n "opencode" README.md AGENTS.md
   ```
   If any cite shifts, do NOT blindly edit at the cited line — re-anchor to the actual line and proceed.
5. Capture the pre-edit `opencode` hit count:
   ```
   grep -rn "opencode" docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md | wc -l
   ```
   Record this number in the docs-consistency sweep table (Step 7). **B4 fix-pass 2 + B2 fix-pass 3:** root `testing-guide.md` and `docs/mcp-integration.md` are now in scope for the sweep (carry 2 LIVE opencode hits each — `testing-guide.md:15,:94`; `docs/mcp-integration.md:364,:366`).

### Step 1 — Refresh `docs/agents.md` §2 table (five rows)

For each of the five agent rows (`developer`, `reviewer`, `tidier`, `approver`, `planner`), update two columns:
- **Role column**: change "via opencode sessions" / equivalent phrasing to the v2 role from the matching `[v2]/meta.json` `description` field (e.g., `developer` becomes "Development orchestrator — plans coding work, delegates to coder/worker, aggregates results"; `reviewer` becomes "Review dispatcher — plans reviews, delegates to skill-equipped workers, convenes governor council for deep review"; `tidier`/`planner` similarly).
- **Key Skills column**: remove `opencode`. Replace with the matching v2 `innate_skills` array (typically `todo, chart, dynamic-skill`).

For each row, prefer copy-pasted phrasing from `agents/<name>[v2]/meta.json` `description` over paraphrase.

### Step 2 — Refresh `docs/agents.md` §3 `meta.json` example

The `meta.json` example block uses `developer` as the example agent. Replace `innate_skills: ["opencode"]` with the v2 pattern `["todo", "chart", "dynamic-skill"]`. Bump `version` from `"1.0.0"` to `"2.0.0"`. Optionally rename the example block caption to "developer v2" to make the version story explicit.

### Step 3 — Refresh `docs/agents.md` §3.4 `soul.md` example block

The example shows "I am a code orchestrator. I control opencode sessions to handle all coding tasks." Replace the "opencode sessions" wording with the v2 wording equivalent (the v2 `developer/soul.md` describes itself as an orchestrator delegating to `coder`/`worker`). Grep `agents/developer[v2]/soul.md` for the actual phrasing and copy verbatim.

### Step 4 — Refresh `docs/agents.md` §3.5 `rule.md` example block

Replace "Spawn opencode for all code operations" with the v2 equivalent rule ("Delegate to `coder` for complex tasks; to `worker` for skill-loaded tasks" or whatever `agents/developer[v2]/rule.md` says — grep-verify at exec time).

### Step 5 — Refresh `docs/agents.md` §7 innate-skills table (line ≈416)

Two changes:
- Remove the `opencode | developer, planner, reviewer, tidier, approver | Controls opencode sessions for code operations` row. Replace with an annotation row or footnote: `opencode | (retired 2026-10-XX; archived to legacy/) | Innate skill decommissioned — agents previously listed here now use v2 tools`. The exact retirement language must mirror the CHANGELOG entry wording.
- Update the §7 example `meta.json` block (≈line 427): `innate_skills: ["opencode", "coordination"]` → use a still-current example like `innate_skills: ["coordination"]` (since `leader` retains `coordination`).

### Step 6 — Refresh `docs/skill-evolution.md` developer meta.json example (≈line 174)

Replace `innate_skills: ["opencode", "dynamic-skill"]` with `innate_skills: ["dynamic-skill"]` for the example. The surrounding prose ("`"dynamic-skill"` in `innate_skills` loads the skill prompt...") is still correct — preserve verbatim.

### Step 7 — Refresh `docs/agent-prompt-writing-guide.md` §9 (≈lines 234–240)

Current §9 explains v1 → v2 migration as a one-off authoring event. Refresh to describe the new steady state: "When an agent in `agents/<name>/` (the base) is byte-identical to `agents/<name>[v2]/` — the typical post-v2-promotion state — treat the v2 dir as the source of truth and the base dir as a frozen mirror. Editing either dir in isolation creates drift; if you must evolve v2 content, decide explicitly whether the base mirrors or diverges and document the choice in `memory.md`."

Also refresh the "If the agent reuses a v1 file (e.g., `reviewer[v2]` pointing at `agents/reviewer/memory.md`), **create a v2-local copy**" advice: post-migration, base/agents/reviewer is the v2 copy, so the cross-version reuse hazard is now INVERTED — `agents/reviewer/` is the base of v2 content; if anyone re-creates a v1-style `reviewer/` (e.g., as a new divergent version), they must NOT silently share files with `reviewer[v2]/`. Document the inversion.

### Step 8 — Refresh `README.md` and `AGENTS.md` (conditional, only if Step 7 sweep finds stale claims)

Default expectation: both files are mostly structural (agent_dir payload examples, agents/ tree comments). If the docs-consistency sweep at Step 9 flags any stale `opencode` claim about our service in either file, fix in-place. Do NOT add a large new section — keep edits minimal and localized.

If neither file mentions `opencode` for our service, leave untouched and annotate "no edit needed" in the sweep table.

### Step 9 — Docs-consistency sweep (mandatory; produces a record; **R10 BINDING GATE supersedes the 4-bucket for active-tree doc rows**)

Run from worktree root. The sweep covers the **FULL** opencode stack (communication service AND session/API coding-worker integration lane) plus the D6 false-positive exclusion list:

```
# Stream (i) — opencode (broad; catches all uses of the word, both streams + false positives)
# B2+B4 fix-pass 3: added root testing-guide.md + docs/mcp-integration.md to the sweep scope.
grep -rn "opencode" docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md

# Stream (ii) — session/API coding-worker integration lane phrases
grep -rnE "opencode (session|sessions|API|api|coding-worker|coding worker|integration lane)" docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md
grep -rnE "coding.worker" docs/ README.md AGENTS.md testing-guide.md docs/mcp-integration.md
```

**Mandatory-FIX rows for `testing-guide.md` (B4 fix-pass 2 — root-level active guidance, NOT allowlist-eligible):**

| Path | Line | Text snippet | R10 disposition | Action |
|---|---|---|---|---|
| `testing-guide.md` | 15 | "Integration tests (requires live OpenCode server)" | MANDATORY FIX (R10 §11.2) | Rephrase: replace "live OpenCode server" with a still-current equivalent (e.g., "live LLM/fenced dev lane" or remove the `integration` marker's opencode reference) |
| `testing-guide.md` | 94 | "Live OpenCode server tests" (the `integration` marker row) | MANDATORY FIX (R10 §11.2) | Rephrase to use a still-current marker description (e.g., "fenced dev lane" or "live LLM" — match the post-migration marker landscape) |
| `docs/mcp-integration.md` | 364 | "#### Example: OpenCode Configuration" | MANDATORY FIX (R10 §11.2; B2 fix-pass 3) | Rephrase or remove: the example points to an opencode-specific config; replace with a still-current MCP example or remove the section |
| `docs/mcp-integration.md` | 366 | "Add to your OpenCode configuration file:" | MANDATORY FIX (R10 §11.2; B2 fix-pass 3) | Rephrase to use a generic MCP transport example (e.g., "Add to your MCP client configuration:") — NOT allowlist-eligible (active doc) |

**R10 (binding user directive, 2026-10-13, architecture-recommendation.md §11) SUPERSEDES the prior 4-bucket classification for active-tree doc rows.** The prior "stale→fix / false-positive→leave / historical→leave / intentional→leave" framing collapses for active-tree doc rows into: **FIX unless allowlisted.** The allowlist mechanism is:

1. Every active-tree doc row that is NOT historical/audit material is a **mandatory FIX** (Step 1–8 edits land).
2. The allowlist rows are reserved for the architecture-recommendation §11.3 exclusion set (D6 false positive, vendored third-party plugins, gitignore, etc.) and the P4 stale-HISTORY rows that the executor justifies per-row.
3. The allowlist file lives at `evidence/r10-allowlist.md` and is consumed by Phase E's FINAL TESTER GATE (phaseE Step 13).

**Mandatory-FIX set (active-tree doc rows, per R10 §11.2):**

| Path | Line(s) cited (verify at exec time) | Nature of fix |
|---|---|---|
| `docs/agents.md` | §2 table rows for developer/reviewer/tidier/approver/planner (≈lines 37, 39, 41, 42, 43); §3 meta.json example (≈line 100); §3.4 soul.md example block (≈lines 152–165, especially 154, 159); §3.5 rule.md example block (≈lines 170–185, especially 178); §7 innate-skills table (≈line 416); §7 meta.json example block (≈lines 425–428) | Replace `opencode` claims with post-Phase-D / v2 reality |
| `docs/skill-evolution.md` | ≈line 174 (the `["opencode", "dynamic-skill"]` example) | Refresh innate_skills array to v2 pattern |
| `docs/agent-prompt-writing-guide.md` | §9 (≈lines 234–240) | Refresh narrative from "v1 → v2 migration happened" to "base := copy of v2; v2 unchanged + still the live path" |
| `docs/test-suite-known-issues.md` | conditional — see classification table below | mixed: live OpenCode test reference becomes historical→leave after Phase A's archive move; out of Phase D scope (Phase C follow-up may rewrite) |
| `README.md` | ≈lines 84, 104–107, 183 (agent_dir payload, agents/ tree example, agents/ comment) | Conditional — only edit if docs-consistency sweep finds a stale claim. Default: structural example refreshes only |
| `AGENTS.md` | ≈line 123 ("Agents live in `agents/<agent_id>/` with files:") | Conditional — annotation only |
| `CHANGELOG.md` | Insert new section under `## [Unreleased]` (≈line 10) | New migration entry per Step 10 |
| `docs/agents.md` §2 — testing-guide.md | (sibling; **NEW R10 row**) | Phase D scope includes testing-guide.md if it carries opencode-era claims; grep-verify at exec time |

**Allowlist rows (the rows that survive as "leave" under R10):**

| Path | Reason (one-sentence justification, required by R10) |
|---|---|
| `docs/mcp-kb-usage.md` | **D6 false positive** (architecture-recommendation §11.4 boundary case #1) — file documents the upstream `sst/opencode` product, not our service. Line-by-line strictly-upstream verification at execution; any our-stack line is FIXED or moved. |
| `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` | **Vendored third-party** (architecture-recommendation §11.4 boundary case #2) — provenance class `copy_freely`; `plugins/` IS shipped in release payloads per `stage.sh:407`; cannot move to `legacy/` without breaking the OD plugin snapshot. NOT a user-decision item. |
| `.gitignore` opencode lines (e.g. `opencode_sessions.db` ignore) | **INTENTIONAL-KEEP** (architecture-recommendation §11.4 boundary case #3) — the `opencode_sessions.db` ignore remains functional for D7 orphan DBs. |
| `.kilo/` (66 lines across tracked files, per W4 dry-run) | **Executor-classified at gate time** (architecture-recommendation §11.4 boundary case #4) — historical tool state → allowlist; active config → fix. Justified per-file at exec time. Gate-excluded via `':(exclude).kilo'` in the R10 FINAL TESTER GATE (per W4 review). |
| `docs/incidents/**` | Historical incident records; out of mission scope. |
| `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` | **Two opencode bug docs moved to `legacy/docs/bugs/`** (per addendum MOVE list). Allowlist justification: the docs were archived; pre-archive they described retired stack behavior. |
| `docs/bugs/agent-call-wrong-tool-spawn-session.md` | Historical bug record (post-fix). Allowlist justification: bug record, not active-stack behavior. |
| `docs/plans/{decouple-execution-plan.md, decouple-review.md, cleanup-old-architecture.md, skill-metrics-improvement.md}` | Historical planning records. Allowlist justification: past planning records; out of mission scope for editing. |
| `docs/test-suite-known-issues.md:43` | Historical test-suite record; pointer to `legacy/tests/opencode/...` post-Phase-A. Allowlist justification: reference to retired test path; the line becomes a historical pointer. |
| `.agents/tester/PACKS.md:922,:957,:1272,:2157` | **P4 stale-HISTORY rows** (architecture-recommendation §6 P4) — reference moved pack baselines; no runner enforces presence. Allowlist justification: stale-HISTORY references to retired pack basenames. **BOTH gate-excluded** via `':(exclude).agents/tester/PACKS.md'` **AND allowlisted** — no contradiction (per W4 review consistency check). |
| `test/packs/regression_integration_test.sh:40` | **P4 stale-HISTORY row** — same as above; allowlist with justification. |

**`docs/bugs` exclusion is CONDITIONAL** (per R10 §11.3): only the two moved opencode bug docs + per-file historical-incident rows justified in the allowlist. **Any `docs/bugs` row describing CURRENT active-stack behavior is FIXED, not excluded.** The executor reviews each `docs/bugs` row at exec time and either justifies it as historical (allowlist) or marks it for fix (mandatory).

**Allowlist file mechanics:**

The committed allowlist file is `evidence/r10-allowlist.md` (hand-authored, executor's job). One row per excluded path with a one-sentence justification. **Rows without justification FAIL gate review** (architecture-recommendation §11.3). Phase E's FINAL TESTER GATE consumes this file.

**The two greps are independent; the union is the residual surface. Both greps must be empty of unclassified hits (post-fix).**

The full classification table for the docs surface (R10-aware; replaces the prior Step 9 table):

| File:line | Text snippet | Stream (i) comm / (ii) lane / neither | R10 disposition | Action | Reason |
|---|---|---|---|---|---|
| `docs/agents.md:37` | "`developer` ... via opencode sessions ... `opencode`" | (ii) lane — "opencode sessions" | MANDATORY FIX | Edit per Step 1 | Active-tree doc; R10 §11.2 mandatory-fix set |
| `docs/agents.md:39` | "`reviewer` ... `opencode`" | both | MANDATORY FIX | Edit per Step 1 | (same) |
| `docs/agents.md:41` | "`tidier` ... `opencode`" | both | MANDATORY FIX | Edit per Step 1 | (same) |
| `docs/agents.md:42` | "`approver` ... `opencode`" | both | MANDATORY FIX | Edit per Step 1 | (same) |
| `docs/agents.md:43` | "`planner` ... `opencode`" | both | MANDATORY FIX | Edit per Step 1 | (same) |
| `docs/agents.md:100` | `"innate_skills": ["opencode"]` | (i) comm | MANDATORY FIX | Edit per Step 2 | Active-tree doc; R10 §11.2 |
| `docs/agents.md:154` | "I control opencode sessions to handle all coding tasks." | (ii) lane | MANDATORY FIX | Edit per Step 3 | Active-tree doc |
| `docs/agents.md:159` | "- Spawning opencode sessions" | (ii) lane | MANDATORY FIX | Edit per Step 3 | (same) |
| `docs/agents.md:178` | "Spawn opencode for all code operations" | (i)+(ii) | MANDATORY FIX | Edit per Step 4 | Active-tree doc |
| `docs/agents.md:416` | "`opencode` \| developer, planner, reviewer, tidier, approver \| Controls opencode sessions for code operations" | (ii) lane | MANDATORY FIX | Edit per Step 5 | Active-tree doc |
| `docs/agents.md:427` | `"innate_skills": ["opencode", "coordination"]` | (i) comm | MANDATORY FIX | Edit per Step 5 | Active-tree doc |
| `docs/skill-evolution.md:174` | `"innate_skills": ["opencode", "dynamic-skill"]` | (i) comm | MANDATORY FIX | Edit per Step 6 | Active-tree doc; R10 §11.2 |
| `docs/mcp-kb-usage.md:12-13` | "`~/.config/opencode/opencode.json`" | neither (false positive) | ALLOWLIST (R10 §11.4 #1) | None — D6 false positive; allowlist row with strictly-upstream verification | Documents upstream sst/opencode config; not our service |
| `docs/mcp-kb-usage.md:34` | `"$schema": "https://opencode.ai/config.json"` | neither (false positive) | ALLOWLIST (R10 §11.4 #1) | None | Same — upstream product |
| `docs/mcp-kb-usage.md:46,48,50,73` | "oh-my-opencode-slim", "ensemble-kb" mcp config | neither (false positive) | ALLOWLIST (R10 §11.4 #1) | None | Upstream product config docs |
| `docs/test-suite-known-issues.md:43` | "`tests/opencode/test_integration.py` (2) \| Live OpenCode server." | (i) comm | ALLOWLIST (post-Phase-A; historical pointer) | None — historical; allowlist row | Reference to retired test path; the line becomes a historical pointer |
| `docs/plans/decouple-execution-plan.md:*` | opencode-era file paths | both — historical file paths | ALLOWLIST (historical planning record) | None | Past planning record; out of mission scope |
| `docs/plans/decouple-review.md:59` | "55 `waiting_for` control-flow read sites across 18 files ... `opencode/state.py`" | both — historical | ALLOWLIST (historical) | None | Past planning record |
| `docs/plans/cleanup-old-architecture.md:250` | file list including `opencode/state.py` | both — historical | ALLOWLIST (historical) | None | Past planning record |
| `docs/plans/skill-metrics-improvement.md:442` | "`opencode` innate skill (for delegated code execution)" | (ii) lane | ALLOWLIST (historical) | None | Past planning record |
| `docs/bugs/agent-call-wrong-tool-spawn-session.md` | agent_id="opencode" tool call | (ii) lane | ALLOWLIST (historical bug record) | None | Past bug record |
| `docs/bugs/opencode-session-manager-memory-leak.md` | session manager memory leak | (ii) lane | ALLOWLIST (moved to `legacy/docs/bugs/` per addendum MOVE list) | None | Two opencode bug docs were archived |
| `docs/bugs/external-opencode-wait-latency.md` | `daemon/tools/external_opencode.py` wait latency | (i) comm | ALLOWLIST (moved to `legacy/docs/bugs/`) | None | (same) |
| `docs/bugs/child-completion-report-lost-under-concurrent-task-processing.md:107` | "current opencode skill uses…" | (i) comm | ALLOWLIST (historical bug record) | None | Past bug record |
| `.agents/tester/PACKS.md:922,:957,:1272,:2157` | (P4 stale-HISTORY) | (i) comm | ALLOWLIST (P4 stale-HISTORY) **AND gate-excluded** (W4 review) | None — referenced moved pack baselines; no runner enforces presence; BOTH gate-excluded AND allowlisted — no contradiction | Stale-HISTORY reference; per architecture-recommendation §6 P4 |
| `test/packs/regression_integration_test.sh:40` | (P4 stale-HISTORY) | (i) comm | ALLOWLIST (P4 stale-HISTORY) | None | (same) |
| `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` | (R10 §11.4 #2) | both — vendored third-party | ALLOWLIST (R10 §11.4 #2) | None — line-by-line strictly-upstream verification at execution; allowlist row with justification | Vendored third-party design-system resource; cannot move to `legacy/` without breaking OD plugin snapshot |
| `.gitignore` opencode lines | (R10 §11.4 #3) | (i) comm | ALLOWLIST (R10 §11.4 #3) | None — INTENTIONAL-KEEP for D7 orphan DBs | Functional ignore for `opencode_sessions.db` |
| `.kilo/` (66 lines across tracked files, per W4 dry-run) | (R10 §11.4 #4) | EXECUTOR-CLASSIFIED at gate time | Per-file: historical → allowlist; active config → fix | Per architecture-recommendation §11.4 #4; gate-excluded via `':(exclude).kilo'` in the R10 FINAL TESTER GATE (per W4 review) |

The sweep table is required output. **Do NOT delete the sweep output — Phase E consumes it as the pre-E baseline and as proof that D did not introduce unclassified residuals in the docs surface.** Phase E's FINAL TESTER GATE (phaseE Step 13) consumes both this table and the `evidence/r10-allowlist.md` file.

### Step 10 — CHANGELOG entry under `[Unreleased]`

After the divider `---` (≈line 10), insert a new section. Do NOT bump the version (per D8 — no live-system contact; this mission rides the next promote). Follow the per-entry attribution format established by the v0.20.0 entry (record `0f328337`).

Suggested wording (executors may refine; must include the facts below):

> ### Changed — agent-base-v2 migration: base agents = copy of [v2] agents; opencode-era base + comm service archived to legacy/ (mission `agent-base-v2 migration`, branch `feature/agent-base-v2`)
>
> - **Phase A — legacy archive.** opencode-era `agents/developer`, `agents/reviewer`, `agents/planner`, `agents/tidier`, `agents/approver` (the v1 base) plus the opencode comm service were copy-verified into `legacy/` (root-level legacy source dir). Pre-mission and post-mission per-dir sorted sha256 manifests re-verified at mission end — archive intact.
> - **Phase B — base := v2 copy.** `agents/developer`, `agents/reviewer`, `agents/planner`, `agents/tidier`, `agents/approver` (the base, NOT the `[v2]` dirs) were replaced byte-for-byte with the content of their matching `[v2]` dirs. `version` in each base meta.json now reads `"2.0.0"`. v2 dirs untouched and remain the live operational lane; existing instances + ensembles notice NOTHING (v2 checksum invariance: pre == post).
> - **Phase C — opencode excision.** daemon code paths that wired opencode service were removed. `default_agent_versions` runtime value unchanged (verified via read-only `GET /api/settings/default-agent-versions`).
> - **Phase D — references + docs refresh.** `docs/agents.md` §2/§3/§3.4/§3.5/§7, `docs/skill-evolution.md` example, `docs/agent-prompt-writing-guide.md` §9 refreshed to describe v2 reality. `docs/mcp-kb-usage.md` verified false positive (documents upstream sst/opencode, not our service) — UNTOUCHED. Drift implications (base is now a frozen mirror of v2) noted for maintainers; not solved in this mission.
> - **Phase E — verification evidence.** Per-dir sha256 manifests prove v2 zero-touch (pre == post), base==v2 (per-agent diff empty), legacy/ archive intact, full test wall green, fenced dev-lane boot-scan clean, `default_agent_versions` runtime invariant. Evidence committed at `.agents/shared/planning/agent-base-v2/evidence/`.
>
> _No version bump (D8: no live-system contact this mission). Migration rides the next promote ceremony via the fast pipeline; stage.sh manifest machinery self-adapts (`agents_tree_sha256` / `agents_manifest` recompute automatically)._

### Step 11 — Commit

```
# B2 fix-pass 3: added docs/mcp-integration.md (active MCP-integration doc — MANDATORY FIX per R10).
# B4 fix-pass 2: added testing-guide.md (root-level active guidance — MANDATORY FIX per R10).
git add docs/agents.md docs/skill-evolution.md docs/agent-prompt-writing-guide.md README.md AGENTS.md CHANGELOG.md testing-guide.md docs/mcp-integration.md
# + docs-consistency sweep output if committed as a file
git commit -m "docs: refresh references for agent-base-v2 migration (Phase D)"
```

Commit on `feature/agent-base-v2`. Single commit. Do NOT push.

## 6. Per-Step Verification

| Step | Verification command | Expected outcome |
|---|---|---|
| 0 | `git status --porcelain` is empty after Step 11 commit | Worktree dirty during the edits; commit restores clean post-Step 11. |
| 1 | `grep -n "opencode" docs/agents.md` returns 0 hits in the §2 table | All five agent rows now reference v2 reality. |
| 2 | `grep -A1 "innate_skills" docs/agents.md` shows the v2 example | No `["opencode"]` left in §3 example. |
| 3 | `grep -n "opencode" docs/agents.md` returns 0 hits in §3.4 | The soul.md example uses v2 wording. |
| 4 | `grep -n "opencode" docs/agents.md` returns 0 hits in §3.5 | The rule.md example uses v2 wording. |
| 5 | `grep -n "opencode" docs/agents.md` returns 0 hits in §7 (table + example) | Innate-skills table has no `opencode` row for our service; example block uses `coordination`. |
| 6 | `grep -n "opencode" docs/skill-evolution.md` returns 0 hits in the example block | Example uses `["dynamic-skill"]`. |
| 7 | `grep -A2 "v1 → v2 Migration" docs/agent-prompt-writing-guide.md` shows the new "base := copy of v2" wording | §9 reflects the new steady state. |
| 8 | `grep -n "opencode" README.md AGENTS.md` returns 0 hits about our service | If hits exist, fixed in Step 8; if not, "no edit needed" noted. |
| 9 | `ls .agents/shared/planning/agent-base-v2/evidence/docs-consistency-sweep.md` (or commit message contains the table) | Sweep output recorded for Phase E. Table covers BOTH retirement streams (i) comm service and (ii) session/API coding-worker integration lane. |
| 10 | `head -30 CHANGELOG.md` shows the new section under `[Unreleased]` and the divider | No version bump. |
| 11 | `git log -1 --oneline` shows the Phase D commit on `feature/agent-base-v2` | Branch + commit clean. |

## 7. Rollback

`git revert <sha>` of the Phase D commit OR `git reset --hard HEAD~1` if Phase D is the last commit on the branch. Phase D is purely doc edits; rollback is local and lossless. The Phase A archive, Phase B base copy, and Phase C daemon changes are NOT rolled back by this — they live on separate commits earlier on the branch.

Whole-mission rollback = fresh branch from `885a19d4f` (Phase E specifies the recipe; Phase D does not commit outside `docs/` + `CHANGELOG.md` + the optional sweep file, so Phase D's contribution is trivially abandonable by deleting the commit).

## 8. Risks & Mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Cited line numbers in this plan drift between plan-write and exec (record pattern — files edit and grow) | medium | Step 0 mandates grep-verify for every cited line. Every step's "Approx line" prefix is a hint, not an anchor. |
| R2 | Docs edits describe a v2 reality that doesn't match what Phase B actually copied | high | Step 0 reads Phase B output (the actual v2 dirs now at base) and Step 1/2 use copy-pasted text from `agents/<name>[v2]/meta.json` rather than paraphrase. |
| R3 | CHANGELOG entry implies a version bump that doesn't happen | low | The entry explicitly states "No version bump" and follows the `[Unreleased]` convention (record `0f328337`). The next promote bumps and the entry is then pointer-annotated. |
| R4 | Touching `docs/mcp-kb-usage.md` (false-positive trap) | low | Out of scope is stated in §2; Step 9 sweep records the false-positive classification so the human reviewer sees it. |
| R4.5 | Touching `docs/test-suite-known-issues.md:43` (historical record) | low | Out of scope is stated in §2; Step 9 sweep records the historical→leave classification. |
| R4.6 | Docs sweep misses session/API-lane phrasing that's worded differently (e.g., "coding worker" with a space, "opencode integration", "opencode API" without "session") | medium | Step 9 runs THREE greps: bare `opencode` + `opencode (session\|sessions\|API\|api\|coding-worker\|coding worker\|integration lane)` + `coding.worker` regex. The union is the residual surface. Re-run after commit and diff against the pre-edit count (must drop). |
| R4.7 | R10 binding gate (active-tree doc row classified as "leave" instead of "fix") | high | R10 §11 supersedes the 4-bucket for active-tree doc rows. Step 9's classification table enforces MANDATORY FIX for every active-tree doc row that is not in the §11.3 allowlist set. The `evidence/r10-allowlist.md` file documents every "leave" row with justification; rows without justification FAIL gate review. Phase E's FINAL TESTER GATE consumes both. |
| R4.8 | Allowlist row for an active-stack `docs/bugs` row slipped through as "historical" | high | R10 §11.3 explicit: "any `docs/bugs` row describing CURRENT active-stack behavior is FIXED, not excluded." Executor reviews each `docs/bugs` row at exec time. The allowlist row for `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` is justified because those files were archived to `legacy/docs/bugs/` (moved, not active). |
| R5 | Edits to `docs/plans/*` or `docs/bugs/*` historical files | low | Out of scope (§2). Step 9 sweep classifies them as historical→leave; any accidental edit is caught by the final `git diff --stat`. |
| R6 | Accidentally editing code or test files | low | §2 lists the exact files in scope; per-step verification greps the docs surface; `git show --stat HEAD` and `git diff --stat HEAD~1..HEAD` (Phase D's own commit only) assert Phase D's commit touches ONLY the enumerated docs files (B3 fix-pass 2 — re-anchored from the cumulative `git diff --stat 885a19d4f..HEAD` check, which necessarily contains A+B+C outputs and can never be satisfied at D-time). |
| R7 | The docs-consistency sweep misses a hit because the grep pattern doesn't match | medium | Pattern is `grep -rn "opencode" docs/ README.md AGENTS.md` — case-sensitive, fixed-strings-to-regex. Re-run after commit and diff against the pre-edit count (must drop). |
| R8 | A `CHANGELOG.md` precedent for "no version bump migration entries" doesn't exist — the format is novel | low | Phrase the entry under `## [Unreleased]` (the canonical pre-bump container per record `0f328337`) and use the existing `### Changed — <title> (<branch/merge>)` shape from the v0.20.0 entry. The reviewer can request wording tweaks during promote. |
| R9 | README.md/AGENTS.md edits become scope creep | low | Conditional step — only edit if sweep finds a stale claim; otherwise annotate "no edit needed". |
| R10 | main checkout (`/home/nea/ensemble-src`) is dirty and any tool that follows a real-path could leak into it | medium | Step 0 confirms `git status --porcelain` from worktree; all commands are cd-rooted in the worktree; the mktemp FAKE_HOME + escape guardrail applies only to test scripts (this phase has no scripts), but conceptually: never run unanchored commands from the main checkout. |

## 9. Definition of Done (Phase D)

Checkable:

- [ ] Every step 1–8 edit landed at the verified line numbers.
- [ ] `grep -rn "opencode" docs/agents.md docs/skill-evolution.md docs/agent-prompt-writing-guide.md` returns 0 hits (the three docs in our scope) — verified at exec time; line numbers may have shifted.
- [ ] `grep -rnE "opencode (session|sessions|API|api|coding-worker|coding worker|integration lane)" docs/agents.md docs/skill-evolution.md docs/agent-prompt-writing-guide.md` returns 0 hits (session/API lane phrasing is also gone) — verified at exec time.
- [ ] `grep -n "opencode" docs/agents.md` is empty.
- [ ] **R10 mandatory-FIX set applied** (R10 §11.2): `docs/agents.md` (8 cited regions), `docs/skill-evolution.md:174`, `docs/agent-prompt-writing-guide.md` §9, **`testing-guide.md` (:15, :94) MANDATORY FIX per B4 fix-pass 2 — root-level active guidance, NOT allowlist-eligible**, `README.md` / `AGENTS.md` (conditional per Step 8) — every active-tree doc row that is not historical/audit is fixed.
- [ ] **R10 allowlist file committed** at `evidence/r10-allowlist.md` with one row per excluded path + one-sentence justification per row. Mandatory rows: `docs/mcp-kb-usage.md` (D6), `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` (R10 §11.4 #2), `.gitignore` opencode lines (R10 §11.4 #3), `.kilo/` (R10 §11.4 #4 — executor-classified), `docs/incidents/**` (historical), `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` (moved to `legacy/docs/bugs/` per addendum MOVE list), `docs/bugs/agent-call-wrong-tool-spawn-session.md` (historical bug record), `docs/plans/*` (historical), `docs/test-suite-known-issues.md:43` (historical pointer), `.agents/tester/PACKS.md:922,:957,:1272,:2157` (P4 stale-HISTORY), `test/packs/regression_integration_test.sh:40` (P4 stale-HISTORY). **Rows without justification FAIL gate review.**
- [ ] Docs-consistency sweep recorded (committed as file under `evidence/` OR appended to the commit message). Table covers the FULL STACK (one package / two roles); every active-tree doc row is MANDATORY FIX; allowlist rows are reserved for the R10 §11.3 exclusion set + P4 stale-HISTORY rows.
- [ ] CHANGELOG entry under `[Unreleased]` inserted; no version bump.
- [ ] **`git show --stat HEAD`** (B3 fix-pass 2) and **`git diff --stat HEAD~1..HEAD`** (Phase D's own commit only) assert Phase D's commit touches ONLY its enumerated files: `docs/`, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `testing-guide.md`, and the optional `evidence/docs-consistency-sweep.md` / `evidence/r10-allowlist.md`. **NOTE (B3 fix-pass 2):** the prior wording "cumulative diff vs 885a19d4f shows ONLY docs/" necessarily FAILS at D-time because the cumulative diff contains A+B+C outputs (`legacy/**`, `agents/<5 base>`, `daemon/**`, `tests/**` [Phase C.3/C.4 edits], `Makefile`/`pyproject.toml`). The phase-scoped verification (`git show --stat HEAD` + `git diff --stat HEAD~1..HEAD`) is the only correct anchor for Phase D.
- [ ] Phase D commit on `feature/agent-base-v2` (NOT pushed).
- [ ] `git log --oneline feature/agent-base-v2 | grep Phase\ D` returns 1 commit.
- [ ] No live-system contact (D8): no API calls, no daemon boot, no promote attempt.
- [ ] Frontend pin (`frontend/jobs.component.ts:2389`) verified-safe — no edit; note in the sweep output.
- [ ] R10 §11.3 exclusion set: every allowlist row is one of (a) D6 false positive, (b) vendored third-party, (c) intentional-keep for D7, (d) historical/audit material, (e) P4 stale-HISTORY with justification. No orphan class remains (per R10 §11.8 census).

## 10. Cross-references (siblings — by filename only)

- `phaseA-legacy-archive.md` — Phase A archives opencode-era base + comm service AND the opencode session/API coding-worker integration lane. Phase D reads the archive path to confirm and annotates the sweep.
- `phaseB-base-copy.md` — Phase B produces base == v2 content. Phase D's docs describe post-Phase-B reality; Phase B's checksum manifests feed Phase E's evidence pack.
- `phaseC-opencode-excision.md` — Phase C excises opencode daemon wiring. Phase D's docs describe post-Phase-C reality; Phase C's boot proof feeds Phase E's boot-scan.
- `phaseE-verification-evidence.md` — Phase E consumes Phase D's docs-consistency sweep output as part of its full-text wall + opencode-residual assertion. The Phase D table covers BOTH retirement streams (i) comm service and (ii) session/API coding-worker integration lane; Phase E re-runs the union of the three grep patterns and asserts zero unclassified residuals in the docs surface.

## 11. Open Items — RESOLVED (addendum landed 2026-10-13)

The leader's exploration addendum (authoritative classification + live-vs-dead verdicts) **landed 2026-10-13** and is incorporated into the current revision of this plan. The previously-parameterized sections (header scope statement, §1 Objective item 1, Step 9 classification table, §9 DoD items, §8 Risks) finalize against the addendum's findings. **No "pending addendum" markers remain in Phase D.**

### O1 — RESOLVED (per addendum VERDICT 1)

The addendum's VERDICT 1 collapsed the prior "two lanes" framing into ONE retirement unit with TWO named roles inside a single package:
- **Session/API role** — the bulk of `daemon/opencode/`
- **Communication role** — the protocol layer INSIDE the session lane

The `daemon/opencode/__init__.py:13` claim "used by the ensemble HTTP router" is STALE. The docs sweep (Step 9) covers the FULL STACK uniformly; the per-row Stream column finalizes as a one-package / two-roles description.

### O2 — RESOLVED (per addendum)

`tests/opencode/` is a single uniform archive tree at `legacy/tests/opencode/` (12 files). The `docs/test-suite-known-issues.md:43` historical→leave annotation in §2 Out of scope is correct as-is.

### O3 — moot

The addendum landed BEFORE finalization. Phase D finalizes with the addendum incorporated. No follow-up delta commit is required for the addendum itself.

### O4 — RESOLVED

The `legacy/` archive root is confirmed. Phase A's plan stores pre-mission archive manifests in the planning dir. Phase D's docs references that anchor.

### O5 — Final additions from the addendum (MCP / agent-facing / InstanceManager surfaces)

Phase D's docs sweep (Step 9) now explicitly covers the FULL STACK surface per the addendum:
- **MCP configs** — per addendum VERDICT 2, NO MCP configs anywhere reference opencode; the docs sweep's docs surface does not need to find any MCP-related stale claims. If a future doc surfaces an MCP-related claim, it joins Step 9's classification table additively.
- **Agent-facing surfaces** — the `opencode` innate skill at `agents/_prompt_system/innate-skills/opencode/` and any `meta.json` listing `innate_skills: ["opencode"]` are part of the docs sweep's "stale→fix" target. The current Step 9 classification table already captures the meta.json hits at `docs/agents.md:100` and `docs/skill-evolution.md:174`.
- **InstanceManager-coordinated resources** — the docs sweep does not typically capture code-side references in `daemon/manager.py` (those are Phase C's surface), but if a doc references the InstanceManager opencode coordination, it joins Step 9 additively. The current `docs/bugs/opencode-session-manager-memory-leak.md` and `docs/bugs/external-opencode-wait-latency.md` are historical→leave and will be archived to `legacy/docs/bugs/` per the addendum MOVE list.

### O6 — If the leader's addendum contradicts a row Phase D committed

The Step 9 classification table is additive; only the Stream and Action columns change for affected rows. The contingency note at the end of Step 9 is now retired (the addendum landed). No phase plans need re-issuing; Phase E's re-grep at its own step 9 catches any newly-classified row in the docs surface and asserts it as part of its own DoD.