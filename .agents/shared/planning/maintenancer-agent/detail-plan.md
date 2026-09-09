# Detail Plan: Maintenancer Agent — File-Level Build Spec

Date: 2026-09-08
Status: APPROVED medium plan → DETAIL build spec for parallel implementation
Branch base: `a6b0ac0fb06edd50d7a0ff7ac11b389e988b6ebe` (release **v0.12.4** from `pyproject.toml`)
Parent planning artifacts: `plan/maintenancer-agent` branch (committed `f40b8b50`)

---

## Preamble — How to read this spec

This is the **single build spec** for the three W1 parallel developers (P1 / P2 / P3) plus W2 (P4) and W3 (P5) implementers. Devs A / B / C branch from this commit and consume only this file plus the three approved parent artifacts on the `plan/maintenancer-agent` branch.

### Consult paths for the approved parent artifacts (DO NOT fork them)

```bash
# Medium plan (approved, 440 lines, 41 tasks, 5 phases, 3 waves)
git -C <worktree> show plan/maintenancer-agent:.agents/shared/planning/maintenancer-agent/plan-overview.md

# Decisions (350 lines, D1–D20 MADE + 5 OPEN adjudicated + reconciliation one-liner)
git -C <worktree> show plan/maintenancer-agent:.agents/shared/planning/maintenancer-agent/decisions.md

# Architect adjudication (268 lines, authoritative layer — fold source)
git -C <worktree> show plan/maintenancer-agent:.agents/shared/planning/maintenancer-agent/architecture-recommendation.md
# Fallback if the local branch is not yet in sync:
git -C <worktree> show origin/plan/maintenancer-agent:.agents/shared/planning/maintenancer-agent/architecture-recommendation.md
```

**Convention v2 cross-reference rule** (writing guide §3): inside any prompt surface (under `agents/maintenancer/`), cross-refs use **section-name + owning agent** form. No `*.md` / `file → Section` path tokens in prompt prose. This detail-plan.md is NOT a prompt surface — file:line citations are the build spec and are intentional here.

### Glossary (terms this spec uses repeatedly)

| Term | Definition |
|------|------------|
| **allow-list** | `meta.json` `tools.allow` array — categories/tools an agent MAY invoke |
| **deny-list** | `meta.json` `tools.deny` array — tools STRIPPED at `resolve_tool_filter` time regardless of category-allow expansion |
| **category** | Tool category string (e.g. `"system-log"`, `"ens-db"`, `"db"`) that expands to a tool-set via `_tool_registry.CATEGORY_MODULES` |
| **privileged category** | Listed in `_tool_registry.PRIVILEGED_TOOL_CATEGORIES`; never default-granted; reachable only via explicit `tools.allow` (R-SR16) |
| **resolution-level enforcement** | A mechanism that runs in `resolve_tool_filter` (at instance build time) — STRIPS before the agent ever sees the tool |
| **defense-in-depth** | A mechanism that runs at CALL time (after resolve) — fires only if the resolution-level mechanism fails or is bypassed |
| **3-factor gate** | `daemon/tools/upgrade_tools.py:1877-2036`: `user_confirmed` + per-instance user-origin window + single-use nonce in real user-origin message row |

### Critical numbers (copy from approved medium plan — DO NOT renegotiate)

- **Waves:** W1 (P1 ∥ P2 ∥ P3, file-disjoint, NO in-wave restart) → W2 (P4, NO restart) → W3 (P5 + ONE restart window)
- **P1-before-P2 merge order invariant** — any crash-restart mid-W1 lands coherent state (P2 cannot land before P1)
- **Tasks total:** 41 (P1 = 11, P2 = 10, P3 = 8, P4 = 7, P5 = 5)
- **MADE decisions:** D1–D20 (architect's `13` count in `architecture-recommendation.md:17` is a pre-fold snapshot — see decisions.md header reconciliation one-liner)
- **OPEN decisions adjudicated:** A (Option 1-refined, D20 enforce), B (Option 1), C (Option 4′), D (Option 1), E (Modified Option 4 tiered)
- **`PRIVILEGED_TOOL_CATEGORIES`** after P2: `frozenset({"system_upgrade", "system-log", "ens-db"})` — at `_tool_registry.py:106-108`
- **Two exact-equality pins that MUST be updated in the SAME P2 PR** (architect §4.4): `tests/unit/tools/test_upgrade_registration.py:100` and `tests/unit/tools/test_attestation_registration.py:154`
- **Detail-Phase Checklist (6 items)** — woven into relevant tasks below, not left as a separate section

---

## File-Disjointness Map — W1

This is the **contract** that lets three developers work in parallel without merge conflicts. Each row's file is touched in EXACTLY one PR. Verify before pushing.

| File | Owner PR | Disjoint? |
|------|---------|----------|
| `agents/maintenancer/meta.json` | **P1** (task 1.2) | ✅ P1 only — P2 task 2.8/2.9 are VERIFICATION-ONLY, no edits |
| `agents/maintenancer/soul.md` | P1 (1.3) | ✅ |
| `agents/maintenancer/rule.md` | P1 (1.4 — placeholder Cardinals) — refined in P4 (4.4) | ✅ (placeholder vs refined — different content) |
| `agents/maintenancer/tools_note.md` | P1 (1.6) | ✅ |
| `agents/maintenancer/workflow.md` | P1 (1.5 — skeleton) — refined in P4 (4.6 dispatcher pattern) | ✅ (skeleton vs dispatcher pattern — different content) |
| `agents/maintenancer/memory.md` | P1 (1.7 — skeleton incl. KB INDEX slot — KB INDEX filled by P3) | ✅ (skeleton + INDEX; KB docs in separate dir) |
| `agents/maintenancer/growth.md` | P1 (1.8) | ✅ |
| `agents/maintenancer/skill-set.yaml` | P1 (1.8 — empty placeholder) — populated by P4 (4.3) | ✅ (empty vs populated) |
| `agents/maintenancer/skills-template/.gitkeep` | P1 (1.8) | ✅ |
| `agents/maintenancer/knowledge/.gitkeep` | P1 (1.8) | ✅ |
| `agents/maintenancer/knowledge/01-architecture-overview.md` | **P3** (3.1) | ✅ |
| `agents/maintenancer/knowledge/02-jobs-missions-admission-state.md` | P3 (3.2) | ✅ |
| `agents/maintenancer/knowledge/03-log-forensics.md` | P3 (3.3) | ✅ |
| `agents/maintenancer/knowledge/04-known-traps.md` | P3 (3.4) | ✅ |
| `agents/maintenancer/knowledge/05-repair-runbooks.md` | P3 (3.5) | ✅ |
| `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md` | P3 (3.6) | ✅ |
| `agents/maintenancer/skills-template/kb-curator.md` | P3 (3.7) | ✅ (P1 creates `skills-template/.gitkeep`; P3 adds the first concrete file) |
| `agents/leader/meta.json` | P1 (1.9 — adds `"maintenancer"` to `team_members`) | ✅ |
| `daemon/tools/ens_db_tools.py` | **P2** (2.1) | ✅ |
| `daemon/tools/_tool_registry.py` | P2 (2.3 CATEGORY_MODULES; 2.4 DYNAMIC_TOOL_NAMES; 2.6 PRIVILEGED_TOOL_CATEGORIES) | ✅ (single file, single PR) |
| `daemon/tools/instance.py` | P2 (2.2 — `create_ens_db_tools` list-append in `create_instance_tools`) | ✅ |
| `agents/developer/meta.json` | P2 (2.7 — remove `system-log`) | ✅ |
| `agents/developer[v2]/meta.json` | P2 (2.7 — remove `system-log`; architect §4.3 versioned-meta guard) | ✅ |
| `agents/wanderer/meta.json` | P2 (2.7 — remove `system-log`) | ✅ |
| `agents/_baby_template/meta.json` | **P2 (2.7a — leader-adjudication #1) — remove `system-log` from illustrative allow-list** | ✅ |
| `agents/developer/{soul,rule,workflow,tools_note}.md` (4 prompt files) | **P2 (2.7b — leader-adjudication #2) — variant-tolerant grep + reword log-forensics instructions** | ✅ (per-PR disjointness: P1 owns `agents/maintenancer/*`; P2 owns `agents/developer/*` rewordings) |
| `agents/developer[v2]/{soul,rule,workflow,tools_note}.md` (4 prompt files) | **P2 (2.7b — leader-adjudication #2 — same grep + reword pattern)** | ✅ |
| `agents/wanderer/{soul,rule,workflow,tools_note}.md` (4 prompt files) | **P2 (2.7b — leader-adjudication #2 — same grep + reword pattern)** | ✅ |
| `agents/worker/{soul,rule,workflow,tools_note}.md` (4 prompt files) | **LEAVE AS-IS (leader-adjudication #2)** — worker retains break-glass access per OPEN B Option 1 | n/a — P2 does NOT touch worker |
| `tests/unit/test_maintenancer_agent.py` | P1 (1.11) | ✅ |
| `tests/unit/tools/test_ens_db_tools_select_only.py` | P2 (2.10a) | ✅ |
| `tests/unit/tools/test_ens_db_repair_audit.py` | P2 (2.10b) | ✅ |
| `tests/unit/tools/test_ens_db_repair_idempotent.py` | P2 (2.10c — checklist item 3) | ✅ |
| `tests/unit/tools/test_ens_db_repair_refuses_non_idempotent.py` | P2 (2.10d — **normalized** from typo `test_ens_db_repair_idempotent_doller.py`) | ✅ |
| `tests/unit/tools/test_privileged_category_system_log.py` | P2 (2.10e) | ✅ |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | P2 (2.10f) | ✅ |
| `tests/integration/test_maintenancer_upgrade_gate_refuses.py` | P2 (2.10g) | ✅ |
| `tests/unit/tools/test_upgrade_registration.py` | P2 (2.6 — same-PR pin update at line 100) | ✅ (single file, single PR) |
| `tests/unit/tools/test_attestation_registration.py` | P2 (2.6 — same-PR pin update at line 154) | ✅ (single file, single PR) |
| `tests/unit/test_maintenancer_kb_coverage.py` | P3 (3.8) | ✅ |
| `tests/unit/test_maintenancer_prompt_compliance.py` | P1 (1.11 extension — runs as part of P1's test suite) | ✅ |

**P4 / P5 file-touch map** (W2 + W3 — sequential, no parallelism required):

| P4 file | Source task |
|--------|------------|
| `agents/maintenancer/skills-template/<7 skill files>` | 4.2 |
| `agents/maintenancer/skill-set.yaml` | 4.3 |
| `agents/maintenancer/rule.md` (refine Cardinal #3 / #4) | 4.4 |
| `agents/maintenancer/workflow.md` (refine dispatcher pattern) | 4.6 |
| `tests/unit/test_maintenancer_skill_versions_consistent.py` | 4.7 |
| `tests/unit/test_maintenancer_report_scrutiny.py` | 4.7 |
| `tests/unit/test_maintenancer_fan_in_valve.py` | 4.7 |
| `tests/unit/test_maintenancer_team_members_within_team.py` | 4.7 |

| P5 file | Source task |
|--------|------------|
| `agents/maintenancer/README.md` | 5.1 |
| `agents/leader/soul.md` (or canonical-home prompt surface) — routing line | 5.3 |
| `agents/maintenancer/README.md` §Rollout (or sibling `ROLLOUT.md`) | 5.4 |
| `tests/integration/test_maintenancer_end_to_end.py` | 5.5 |

---

## Merge Order Invariant (P1-before-P2)

**Hard rule:** P1 PR merges BEFORE P2 PR. This is enforced architecturally — any crash-restart between the two merges lands coherent state because:

- The running process keeps the OLD `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade"})` — `system-log` is NOT yet privileged — stripping does not apply — explicit allows in developer/wanderer/worker still resolve — NO exclusivity gap.
- The new category strings in P1 task 1.2's `tools.allow` (`"ens-db"`, `"system_upgrade"`, `"db"`, `"knowledge"`) are INERT until P2 lands — they trigger a validation warning only (`registry.py:1178-1180`).
- P2's de-scopes (remove `system-log` from developer/v2/wanderer) and frozenset promotion land ATOMICALLY when P2 merges — the next restart picks them both up together.

**Verification BEFORE merging P2** (must hold true):

- [ ] P1 PR is merged on `feature/maintenancer-agent`
- [ ] `agents/maintenancer/meta.json` has `tools.allow: ["system-log","ens-db","knowledge","system_upgrade","db"]` and `tools.deny: ["git_commit","edit_file","write_file","system_restart"]`
- [ ] `agents/leader/meta.json` `team_members` contains `"maintenancer"`
- [ ] `tests/unit/test_maintenancer_agent.py` is green in CI

---

## Post-Merge Verification — Tasks 2.8 / 2.9

**Both 2.8 and 2.9 are VERIFICATION-ONLY tasks.** They DO NOT modify `agents/maintenancer/meta.json` (already authored in P1 task 1.2). They run AFTER P1 merges and BEFORE W3 deploys.

### Task 2.8 — Verification + Assertion

**File:** NO new file (uses existing `tests/integration/test_maintenancer_spawn_resolves_tools.py` from P2 task 2.10f)
**Acceptance:**
- Resolves the allow-list via `resolve_tool_filter` for the maintenance agent id and asserts:
  - (a) `system_upgrade` IS in resolved set
  - (b) **`system_restart` is EXCLUDED from the resolved set via `tools.deny` resolution-level strip at `resolve_tool_filter` time** (D20 enforcement — `system_restart` carries `@register_tool_category("system_upgrade")` at `daemon/tools/upgrade_tools.py:1435` so the deny-list strip is the only resolution-level mechanism)
  - (c) `db` category resolves ONLY to user-registered external connections (NOT `ensemble_prod` — Risk R13 standing guard)
  - (d) `ens-db` + `system-log` + `knowledge` resolve cleanly
- Documented in PR description as "verification only — meta.json authoritative source is task 1.2"

### Task 2.9 — Verification + Assertion (deny-list enforcement)

**File:** NO new file (asserts in the same `tests/integration/test_maintenancer_spawn_resolves_tools.py` or a sibling assertion block)
**Acceptance:**
- Asserts the deny-list is enforced at tool-resolution time
- Asserts a worker whose skill fails to load still cannot mutate state via `git_commit`/`edit_file`/`write_file`/`system_restart`
- Documented in PR description as "verification only — meta.json authoritative source is task 1.2"

---

# W1-P1 — Scaffolding + Registration (worktree A)

**Owner:** developer A
**Branch:** `feature/maintenancer-agent-p1-scaffolding` (or per giter convention)
**Target merge:** BEFORE P2 PR (architect §2.3 merge-order invariant)

## P1 Task 1.1 — Create directory skeleton

**Files to create (placeholders OK; substantive content lands in subsequent tasks):**

- `agents/maintenancer/meta.json` (substantive in 1.2)
- `agents/maintenancer/soul.md` (substantive in 1.3)
- `agents/maintenancer/rule.md` (substantive in 1.4)
- `agents/maintenancer/tools_note.md` (substantive in 1.6)
- `agents/maintenancer/workflow.md` (skeleton in 1.5; refined in P4 task 4.6)
- `agents/maintenancer/memory.md` (substantive in 1.7 — KB INDEX slot)
- `agents/maintenancer/growth.md` (1.8)
- `agents/maintenancer/skill-set.yaml` (empty in 1.8; populated in P4 task 4.3)
- `agents/maintenancer/skills-template/.gitkeep`
- `agents/maintenancer/knowledge/.gitkeep`

**Acceptance:** directory layout matches `agents/tester/` pattern (v2 convention).

**Template reference:** `agents/_baby_template/` provides the 8 skeleton files; copy structure but populate per tasks 1.2–1.8.

## P1 Task 1.2 — Author `agents/maintenancer/meta.json` (CRITICAL — author source of truth)

**File:** `agents/maintenancer/meta.json` — JSON object, exact fields per below.

**Required fields (exact):**

| Field | Value | Rationale |
|-------|-------|-----------|
| `id` | `"maintenancer"` | matches directory name |
| `name` | `"Maintenancer"` | display name |
| `version` | `"1.0.0"` | initial |
| `innate_skills` | `["dynamic-skill", "todo", "chart"]` | worker/leader convention |
| `skill_injection` | `true` | v2 dispatcher pattern |
| `no_force_explore` | `true` | KB-targeted work; force-explore wastes tokens |
| `tools.allow` | `["system-log", "ens-db", "knowledge", "system_upgrade", "db"]` | see rationale below |
| `tools.deny` | `["git_commit", "edit_file", "write_file", "system_restart"]` | D20 — `system_restart` is the approver-fix resolution-level strip |
| `team_members` | `["explorer", "worker", "coder"]` | OPEN B Option 1 — worker is designated `system-log` break-glass |

**`tools.allow` rationale (per task 1.2 + D20):**
- `"system-log"` — log forensics (privileged, post-P2; explicit allow reaches it)
- `"ens-db"` — direct `ensemble_prod` access (privileged, post-P2)
- `"knowledge"` — RAG mirror first-turn duty in `workflow.md` (Tier 3 per architect §6.2; without this, the RAG leg is structurally dead — `knowledge_tools.py:677,915`)
- `"system_upgrade"` — GRANTED under 3-factor nonce gate per OPEN A Option 1-refined + architect §5.1+§5.3 (`upgrade_tools.py:1870-1886` shows the gate is instance-bound and satisfiable by a legitimate maintenancer via in-thread user nonce echo)
- `"db"` — GRANTED per user verbatim clause ("it have general db tools too") per D19 — for external user-registered connections only

**`tools.deny` rationale (D20 — approver-fix pass structural-defect correction):**

`system_restart` carries `@register_tool_category("system_upgrade")` at `daemon/tools/upgrade_tools.py:1435` — a category-level grant of `system_upgrade` expands to include `system_restart`. The call-time refusal at `upgrade_tools.py:1458-1465` (`if self_env == "live"`) is **env-conditional** and does NOT fire on dev/demo installs (where the tool would otherwise resolve AND execute). The `tools.deny` entry is the **only resolution-level enforcement** that survives category-grant expansions and works on dev/demo.

**Acceptance:**
- `meta.json` validates against JSON schema (any parser)
- `tools.allow` includes `"system_upgrade"` AND `"db"` (pin test 2.10f asserts both resolve)
- `tools.deny` includes `"system_restart"` (pin test 2.10f asserts it is EXCLUDED from resolved set on dev/demo AND live)
- `db` category resolves to user-registered external connections ONLY (Risk R13 — NOT `ensemble_prod`)

## P1 Task 1.3 — Author `agents/maintenancer/soul.md`

**File:** `agents/maintenancer/soul.md`

**Required content (per writing guide §1, §2, §5):**
- **Identity** (first-person, agent-POV): centralized repair agent for the ensemble daemon. Writes "I" / "my".
- **Tone block** (per §5):
  - **Voice to caller:** terse, evidence-cited, severity-labeled (🔴 non-negotiable / 🟢 advisory).
  - **Voice in dispatch prompts:** imperative, self-contained (the worker reads only its own message).
  - **Per-severity framing:** 🔴 = concrete risk stated; 🟢 = invites, doesn't demand.
- **Output template shape** lives here ONLY (canonical home per §2).

**Compliance:**
- NO tokens in prose: `meta.json`, `daemon/`, `tools.allow`, `_tool_registry`, `skill-set.yaml`, `innate_skills`, `default_agent_versions`, `seed_all`, `agent_id=`
- NO path tokens in cross-references (writing guide §3 convention v2)
- ≤2k chars target

## P1 Task 1.4 — Author `agents/maintenancer/rule.md` (placeholder Cardinals)

**File:** `agents/maintenancer/rule.md`

**Required content:**
- **≤7 Cardinal rules** (writing guide §3 — flat 30-rule lists dilute load-bearing invariants)
- **Guidelines section** (numbered, secondary)

**Proposed Cardinals (refined wording lands in P4 task 4.4):**

1. Read KB before any action
2. Confirm before destructive operation
3. End turn after dispatch (writing guide §7)
4. **Relay nonce verbatim — never fabricate/echo agent-side** (OPEN A Option 1-refined; D20 enforcement via `tools.deny`)
5. Report sanity scrutiny on every worker report (writing guide §7)
6. SELECT-only on `ens_db_postgres_select`
7. Audit-stamp every repair row

**Compliance:** grep `\.md` over `agents/maintenancer/` returns zero path tokens in prose.

**Note:** P1 author writes the placeholder Cardinal #4 above. **P4 task 4.4 owns the final wording** (per approver directive "Cardinal #4 wording (owned by task 4.4)" — leave as-is in P1).

## P1 Task 1.5 — Author `agents/maintenancer/workflow.md` (skeleton)

**File:** `agents/maintenancer/workflow.md`

**Required content (P1 skeleton; P4 task 4.6 refines dispatcher pattern):**
- **Investigation flow** (high-level)
- **Dispatch pattern placeholder** (P4 task 4.6 fills with `send_message(load_skill="<one>")` specifics)
- **Fan-in escape valve placeholder** (P4 task 4.6 fills with stuck-confirm → 1 re-dispatch → `[incomplete]` + `### Gaps` ladder; max-re-dispatch cap)
- **Report-sanity scrutiny placeholder** (P4 task 4.6 fills with `[REPORT SANITY: …]` marker conditioning)
- **END TURN contract stated ONCE** (writing guide §7)

## P1 Task 1.6 — Author `agents/maintenancer/tools_note.md`

**File:** `agents/maintenancer/tools_note.md`

**Required content:**
- **Per-tool allow-list table** (operational, NOT registry-referencing — writing guide §4)
- **`ens_db_*` usage notes:** `ens_db_postgres_select` SELECT-only; `ens_db_repair_execute` 3 gates (confirmation, audit-stamp, dry-run-first); `ens_db_inspect` schema/range; `ens_db_pool_status` diagnostic
- **`system-log` exclusivity reminder:** time-bracket forensics rule applies (line numbers NOT chronological)
- **`system_upgrade` gate reminder:** 3-factor nonce gate per OPEN A — Cardinal rule #4 in 1.4; `system_restart` excluded via `tools.deny` (D20)
- **`db` category reminder:** external connections ONLY — NEVER `ensemble_prod` (Risk R13)
- **`ens-db` vs `db` separation:** daemon's own DB via `ens_db_*` only; user-registered external DBs via `db` only

**Acceptance:** allow-list table matches `meta.json` `tools.allow` (no drift); no registry-prose leakage (writing guide §4).

## P1 Task 1.7 — Author `agents/maintenancer/memory.md` (KB INDEX slot + skeleton)

**File:** `agents/maintenancer/memory.md`

**Required content:**
- **(i) KB INDEX** (load-bearing, per architect §6.2 Tier 1) — one-line-per-doc triggers + verification discipline for the 6 KB docs (≤<1k chars total; guaranteed-on via the loader at `daemon/loader.py:436-441`)
- **(ii) Calibration tables** (placeholder; refined in P4)
- **(iii) Trigger checklists** (placeholder; refined in P4)
- **(iv) Fallback ladder** per writing guide §8 (fallback stays within `team_members`)

**KB INDEX format (example for P1 skeleton — P3 fills content):**

```
## KB Index (load-bearing — verified on first turn)

- 01-architecture-overview.md → trigger: any daemon/manager/graph/loader question; verify-against: v0.12.4 (pyproject.toml)
- 02-jobs-missions-admission-state.md → trigger: any job/task/mission question; verify-against: v0.12.4
- 03-log-forensics.md → trigger: log forensics, line-number trap; verify-against: v0.12.4
- 04-known-traps.md → trigger: traps (SQLite relic, migrations runner, etc.); verify-against: v0.12.4
- 05-repair-runbooks.md → trigger: pause-first, DROP NOT NULL, MaintenanceJob; verify-against: v0.12.4
- 06-restart-upgrade-runbook.md → trigger: 3-factor gate, atomic flip, journal sweep; verify-against: v0.12.4
```

**Acceptance:** KB index is reachable in the assembled prompt (integration test 5.5); no process mechanics duplicated from `workflow.md`; index does NOT duplicate KB content (only lists triggers + verification pointers).

**Note:** P1 writes the SKELETON INDEX with the 6 doc filenames + generic "verify-against: v<version>" placeholders. **P3 task 3.7 + 3.8 fills the actual KB docs** AND refines the INDEX content per each doc's actual content. P1 and P3 do NOT collide on `memory.md` — P3 should NOT edit `memory.md`; instead, P1's INDEX is consumed verbatim by P3's KB content authoring.

## P1 Task 1.8 — Author `agents/maintenancer/growth.md` + `skill-set.yaml` (empty placeholder) + `.gitkeep` files

**Files:**
- `agents/maintenancer/growth.md` — minimal growth/calibration notes (per tester pattern)
- `agents/maintenancer/skill-set.yaml` — empty manifest (P4 task 4.3 populates with 7 skill entries)
- `agents/maintenancer/skills-template/.gitkeep` — placeholder for P3/P4 to add files
- `agents/maintenancer/knowledge/.gitkeep` — placeholder for P3 to add 6 KB docs

**Acceptance:** file structure matches `agents/tester/`.

## P1 Task 1.9 — Edit `agents/leader/meta.json`

**File:** `agents/leader/meta.json`

**Edit:** `team_members` array — append `"maintenancer"`. Current list length 13 → 14 after edit.

**Current list (per `agents/leader/meta.json:17`):** `["planner", "developer", "reviewer", "tidier", "approver", "architect", "tester", "giter", "devops", "explorer", "wanderer", "kb-writer", "doc-writer"]`

**After edit:** append `"maintenancer"` (alphabetical or append — match whatever convention the team uses; do not reorder existing entries).

**Acceptance:** `meta.json` validates; `team_members` includes `"maintenancer"`.

## P1 Task 1.10 — MERGE-ORDER INVARIANT (PR description, not code)

**Artifact:** PR description (NOT a file in the codebase).

**Required text in P1 PR description:**

> **MERGE-ORDER INVARIANT:** This PR must merge BEFORE the P2 PR. Any crash-restart between the two merges lands coherent state — the running process keeps the old frozenset (system-log NOT yet privileged), so de-scopes have no exclusivity gap; the new category strings in `agents/maintenancer/meta.json` `tools.allow` are inert until P2 lands (validation warning only, `daemon/registry.py:1178-1180`). Restart is deferred to Wave 3 (single deploy window) per architect §2.3.

**Acceptance:** PR description contains the text above; giter records merge-order in the merge queue.

## P1 Task 1.11 — Pin test `tests/unit/test_maintenancer_agent.py`

**File:** `tests/unit/test_maintenancer_agent.py` (NEW)

**Pattern:** model on `tests/unit/test_project_manager_agent.py:275-281`

**Required assertions:**

| # | Assertion | Reference |
|---|-----------|-----------|
| 1 | `maintenancer` agent_id resolves via `get_resolved()` | P1 1.11 |
| 2 | `maintenancer[v2]` agent_id resolves (if versioned) | P1 1.11 EXTENDED per architect §4.3 |
| 3 | `version: "1.0.0"` resolves | P1 1.11 |
| 4 | `team_members` matches `meta.json` exactly: `["explorer", "worker", "coder"]` | P1 1.11 |
| 5 | All 8 prompt files load (soul, rule, workflow, memory, tools_note, growth, skill-set.yaml, skills-template/) | P1 1.11 |
| 6 | `tools.allow` includes `"system-log"`, `"ens-db"`, `"knowledge"`, `"system_upgrade"`, `"db"` | P1 1.11 + D20 |
| 7 | `tools.deny` includes `"system_restart"` | P1 1.11 + D20 (resolution-level strip) |
| 8 | Forbidden-token grep returns ZERO hits across prompt prose: `meta.json`, `daemon/`, `_tool_registry`, `tools.allow`, `skill-set.yaml`, `seed_all`, `innate_skills`, `default_agent_versions` | writing guide §1 |
| 9 | Cardinal rules ≤7 | writing guide §3 |
| 10 | END TURN + escape valve + report-sanity markers present in `workflow.md` skeleton | writing guide §7 |

**EXTENDED (architect §4.3):** the test must enumerate BASE + VERSIONED metas (standing guard for future `developer[v3]`-class additions). Use `_check_team_membership` / `get_version(...) or get_resolved(...)` per `daemon/tools/instance.py:4539-4541`.

**Acceptance:** test passes locally + in CI.

---

# W1-P2 — Tool Layer + Exclusivity (worktree B)

**Owner:** developer B
**Branch:** `feature/maintenancer-agent-p2-tool-layer` (or per giter convention)
**Target merge:** AFTER P1 PR merges (architect §2.3 merge-order invariant)

## P2 Task 2.1 — Author `daemon/tools/ens_db_tools.py` (CRITICAL — tool body)

**File:** `daemon/tools/ens_db_tools.py` (NEW)

**Pattern:** mirror `create_system_log_tools` at `daemon/tools/system_log_tools.py:256`.

**Required structure:**

```python
# Module docstring: lists the 4 tools + category + kill-switch + audit contract
# Imports
# KILL_SWITCH_ENV = "ENSEMBLE_REPAIR_ENABLED"  (checklist item 1)
# @register_tool_category("ens-db") ABOVE @tool on every closure

def create_ens_db_tools(manager, current_instance_id):
    """Returns [ens_db_postgres_select, ens_db_inspect, ens_db_repair_execute, ens_db_pool_status]."""
    # factory closure body
```

**Tool 1 — `ens_db_postgres_select` (SELECT-only reader):**
- Uses **shared `manager.engine`** (`InstanceManager.engine` per `manager.py:2043-2049`; ONE engine daemon-wide; pool 5+10 pre-ping at `repositories/factory.py:202-208`)
- `_validate_select_only` enforced (`db_tools.py:97`) — INSERT/UPDATE/DELETE/DDL all raise `SelectOnlyViolation`
- **Per-transaction `SET LOCAL statement_timeout` INSIDE the tool's own connection block** + client-side `asyncio.wait_for(asyncio.to_thread(...))` belt (architect §3.1; thread-bridge cancels the awaitable but does NOT abort the blocking psycopg call)
- Async→sync bridge via `asyncio.to_thread` (`daemon/services/maintenance.py:230, 282` precedent)

**Tool 2 — `ens_db_inspect` (read-only schema inspector):**
- Public-schema tables only: `table_schema='public'` filter
- Row counts via `pg_stat_user_tables.n_live_tup` (approximate)
- Column types, indexes, FK chains, pool status, last-ANALYZE/autovacuum timestamps
- **Withhold:** credentials (return `has_password: bool` only — `db_tools.py:432-434` precedent), role grants, `pg_catalog` noise, other backends' SQL text
- SELECT-only by guard

**Tool 3 — `ens_db_repair_execute` (controlled writer — MOST COMPLEX):**
- **Kill-switch env (checklist item 1):** `ENSEMBLE_REPAIR_ENABLED` (default off in dev; must be on in live). Flag-OFF = byte-identical pin test (per repo convention "kill-switch OFF = byte-identical regression test")
- Uses **DEDICATED `create_engine`** with pool **2+3**, `pool_pre_ping=True`, `pool_recycle=3600`, **`pool_timeout=10s`** (LEADER ADJUDICATION #5 — aligned with `lock_timeout=10s` initial; on timeout the tool returns a clear "repair pool saturated — a repair is in flight; retry when it completes" error; rationale: repairs are rare, human-initiated, idempotent — SERIALIZED is the desired semantics; no queueing pileup)
- Engine-level connect_args: `statement_timeout=60s`, `lock_timeout=10s` (initial — re-tune after first soak per architect §10), `idle_in_transaction_session_timeout=60s`
- **3 gates:** (i) explicit confirmation; (ii) audit `INSERT` in SAME `BEGIN/COMMIT` as the repair, same connection — **fail-closed on audit-write failure** (architect §3.2); (iii) dry-run-first toggle
- **`repair_log` table creation (LEADER ADJUDICATION #4):** **SQLAlchemy model + `create_all`**, NO hand-written migration, NO `_ensure_postgres_columns` entry. Rationale: new TABLE (not column-adds on existing table) → `create_all` creates it on BOTH engines (SQLite + PG) even against existing DBs (existence-checked); `_ensure_postgres_columns` exists for column-adds; migrations runner is SQLite-only by design (`runner.py:486-491`). Pickup verified in 2.10h dual-engine coverage.
- **Audit record shape** (`repair_log` SQLAlchemy model — mirror `daemon/repositories/infra/models.py:301-394` `InfraAssetHistory`: append-only, JSONB snapshot, `changed_by`, indexed on `(target, timestamp)`): `id, created_at, created_by_instance_id, created_by_agent_id, sql_text, sql_class, sql_hash, dry_run, before_snapshot (JSONB), after_snapshot (JSONB), outcome ('previewed'|'committed'|'rolled_back'|'error'), error_message, target_table, nonce, ttl_expires_at`
- DML pre-image via shadow `SELECT ... FOR UPDATE`; post-image via `RETURNING *` (same tx)
- DDL records `sql_class` + `DO$$` block hash + dry-run preview result
- **Dry-run wrappers:** DDL = `BEGIN; <DDL>; ROLLBACK;`; DML = same with row-count projection + first ~100 affected-row preview
- **Confirm gate:** single-use nonce + 5-min TTL + action-bound (`kind='repair', target=<table/operation>`) — borrowed from `upgrade_tools.py:1937-2006` primitives but WITHOUT human-relay factor
- **Idempotent `DO$$`-only DML** — partial application must be reconstructible from `repair_log`
- **Tool refuses any `*.sql` filename input** (migration-runner trap)
- **Repository-methods-first rule** — surface "a service method exists for this class" warning in dry-run output (architect §3.4)
- **Self-surgery refusal** — guard refuses targets matching the calling instance's own rows (architect §7.2)
- **NEVER register `ensemble_prod` in `ConnectionPoolManager`** (Risk R13 — R-13 standing guard)

**Tool 4 — `ens_db_pool_status` (diagnostic):**
- Returns pool counts (`engine.pool.status()`)
- Read-only

**Acceptance:**
- Tool bodies respect §3 SELECT guard
- Per-tx `SET LOCAL` applied inside shared-engine tool's own connection block
- Repair body has 3 gates + same-tx audit + idempotent `DO$$`-only
- Async→sync bridge via `asyncio.to_thread` works
- Kill-switch flag-OFF returns byte-identical (no-op) response

## P2 Task 2.2 — Wire `create_ens_db_tools` into `daemon/tools/instance.py:create_instance_tools`

**File:** `daemon/tools/instance.py` — append after the existing `create_upgrade_tools` list-append at line ~4441 (per digest)

**Edit:**
- Add `from .ens_db_tools import create_ens_db_tools` import near line 212 (where `create_system_log_tools` and `create_upgrade_tools` are imported)
- In `create_instance_tools` (line ~1753), add the list-append: `tools.extend(create_ens_db_tools(manager, current_instance_id))` after the `create_upgrade_tools` extend

**Acceptance:** `_tool_registry.known_tool_names()` resolves all 4 `ens_db_*` tool names; per-agent resolution finds them for maintenance.

**Pattern reference:** `upgrade_tools.py:117` + `instance.py:4431,4441` — full 10-step checklist at `upgrade_tools.py:110-142`.

## P2 Task 2.3 — Add `CATEGORY_MODULES` entry

**File:** `daemon/tools/_tool_registry.py:494` (per digest)

**Edit:** add line `"ens-db": "daemon.tools.ens_db_tools"` to the `CATEGORY_MODULES` dict.

**Acceptance:** registry entry added; category string `"ens-db"` resolves via `_tool_registry.get_category_tool_names()`.

## P2 Task 2.4 — Add 4 tool names to `DYNAMIC_TOOL_NAMES`

**File:** `daemon/tools/_tool_registry.py:23` (per current source)

**Edit:** add to `DYNAMIC_TOOL_NAMES: frozenset[str] = frozenset({...})`:
- `"ens_db_postgres_select"`
- `"ens_db_inspect"`
- `"ens_db_repair_execute"`
- `"ens_db_pool_status"`

**Acceptance:** factory-created names registered; `KNOWN_TOOL_NAMES` regen will pick them up.

## P2 Task 2.5 — Regen `KNOWN_TOOL_NAMES` via `discover_source_only_tool_names()`

**Command (per `upgrade_tools.py:124-125` recipe):**

```bash
uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"
```

**Then:** paste the output into `_tool_registry.py` `KNOWN_TOOL_NAMES` literal.

**Pin test:** `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` passes.

**Acceptance:** drift test passes in CI.

## P2 Task 2.6 — Edit `PRIVILEGED_TOOL_CATEGORIES` frozenset + SAME-PR pin updates

**File 1:** `daemon/tools/_tool_registry.py:106-108`

**Edit:**
```python
PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({
    "system_upgrade",
    "system-log",
    "ens-db",
})
```

Update the comment block above (lines 94-105) to reflect three categories.

**File 2 (SAME PR):** `tests/unit/tools/test_upgrade_registration.py:100`

**Edit:** update the exact-equality pin assertion from `frozenset({"system_upgrade"})` to `frozenset({"system_upgrade", "system-log", "ens-db"})`. This is a **deliberate silent-additions-visible pin** (architect §4.4) — updating it in the same PR is the mechanism working as designed.

**File 3 (SAME PR):** `tests/unit/tools/test_attestation_registration.py:154`

**Edit:** same update — from `frozenset({"system_upgrade"})` to `frozenset({"system_upgrade", "system-log", "ens-db"})`.

**Acceptance:**
- `_strip_privileged_category_tools` (`instance.py:4497-4514`) now strips all 3 categories for empty-allow paths
- Comment block above updated to reflect three categories
- Both pin tests updated in the SAME PR

## P2 Task 2.7 — De-scope `system-log` from developer / developer[v2] / wanderer

**File 1:** `agents/developer/meta.json` — remove `"system-log"` from `tools.allow` array
**File 2:** `agents/developer[v2]/meta.json` — remove `"system-log"` from `tools.allow` array (architect §4.3 — versioned-meta resolution prefers `get_version(...) or get_resolved(...)` at `instance.py:4539-4541`; de-scoping base developer only leaves `developer[v2]` instances holding logs)
**File 3:** `agents/wanderer/meta.json` — remove `"system-log"` from `tools.allow` array

**Worker** (`agents/worker/meta.json`) keeps `system-log` per OPEN B Option 1 (designated break-glass — architect §4.5, §7.1). **Watcher** untouched — loses `system-log` by R-SR16 side-effect (empty-allow default-open hole closes).

**Acceptance:** resolved tool sets for developer base + developer[v2] + wanderer exclude `ens_system_log_*`; resolved tool set for worker includes `ens_system_log_*` but excludes `ens_db_*`.

## P2 Task 2.7a — Clean `agents/_baby_template/` illustrative allow-list (LEADER ADJUDICATION #1)

**Rationale (leader adjudication #1):** **NOT just hygiene** — privileged categories are reachable ONLY via explicit allow (architect §4.1, `instance.py:316-322`). A copied template entry would GRANT logs to every future agent instantiated from this template — that's the centralization leak. This is structural, not cosmetic.

**File:** `agents/_baby_template/meta.json`

**Edit:** remove `"system-log"` from the illustrative `tools.allow` example. The template's example allow-list is now representative — it does NOT contain any `PRIVILEGED_TOOL_CATEGORIES` member.

**Acceptance:** `agents/_baby_template/meta.json`'s `tools.allow` contains ZERO entries from the `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade", "system-log", "ens-db"})` set. Regression test: `tests/unit/tools/test_privileged_category_system_log.py` (extended, see task 2.10e extension below) MUST additionally assert that NO meta.json under `agents/*/meta.json` or `agents/_baby_template/meta.json` contains a privileged category in `tools.allow` WITHOUT the corresponding allow being explicit-and-justified (sample check — full audit is a separate grep test in 2.10e).

## P2 Task 2.7b — Reword log-forensics instructions in de-scoped agent prompts (LEADER ADJUDICATION #2)

**Rationale (leader adjudication #2):** audit developer / developer[v2] / wanderer prompt files for log-forensics usage instructions; remove or reword to "delegate ensemble log forensics to maintenancer". Worker's references STAY (break-glass per OPEN B).

**Files (12 prompt files, P2 owns):**

| Agent | Files |
|-------|-------|
| developer | `agents/developer/{soul,rule,workflow,tools_note}.md` |
| developer[v2] | `agents/developer[v2]/{soul,rule,workflow,tools_note}.md` |
| wanderer | `agents/wanderer/{soul,rule,workflow,tools_note}.md` |

**Variant-tolerant grep pattern** (per repo convention "Variant-Tolerant Corpus Sweeps" — grep BOTH spaced and unspaced forms; enumerate-by-grep closure):
```
grep -RE "ens_system_log[a-z_]*|system[ -]log|log forensics" agents/developer/ agents/developer\[v2\]/ agents/wanderer/ --include="*.md"
```

**Reword rule:** every match becomes either (a) "delegate to maintenancer for ensemble log forensics" (with reference to maintenancer's KB-03 `log-forensics` skill) or (b) removal if the original prose was only a capability claim without concrete use instructions.

**Worker's prompt files** (`agents/worker/{soul,rule,workflow,tools_note}.md`) are LEFT AS-IS — worker retains break-glass access per OPEN B Option 1 and any existing log-forensics instructions remain valid.

**Acceptance:** post-grep returns ZERO matches for `ens_system_log_*` / `system-log` / "log forensics" usage instructions across the 12 reworded prompt files (zero-count closure — enumerate-by-grep, not caller's item count). Worker's prompt files retain their existing references.

## P2 Task 2.8 — VERIFICATION + ASSERTION (no new file; runs in P2 task 2.10f)

See **"Post-Merge Verification — Tasks 2.8 / 2.9"** above. This is NOT a file-authoring task — it is a verification step that runs after P1 merges.

## P2 Task 2.9 — VERIFICATION + ASSERTION (deny-list enforcement; no new file)

See **"Post-Merge Verification — Tasks 2.8 / 2.9"** above.

## P2 Task 2.10 — All test files (8 NEW test files + 2 same-PR pin updates)

### P2 Task 2.10a — `tests/unit/tools/test_ens_db_tools_select_only.py` (NEW)

**Pattern:** mirror `daemon/tools/db_tools.py:497` (cited as `tests/unit/tools/test_db_tools.py:497` in the medium plan — checklist item 4 corrects this to `daemon/tools/db_tools.py:497`)

**Required assertions:**
- INSERT / UPDATE / DELETE / DDL all raise `SelectOnlyViolation`
- **Per-tx `SET LOCAL statement_timeout` is applied INSIDE the tool's own connection block** (architect §3.1) — verify via SQL trace or `pg_stat_activity`
- Run on **BOTH SQLite-backed AND PG-backed test fixtures** (architect §7.5 — checklist item 2)

### P2 Task 2.10b — `tests/unit/tools/test_ens_db_repair_audit.py` (NEW)

**Required assertions:**
- `repair_log` row stamped `created_by=current_instance_id`
- Audit `INSERT` in SAME transaction as repair (fail-closed on audit-write failure per architect §3.2)
- **Council disagreement noted (checklist item 4):** `infra.py:226-234` vs `infra/models.py:256 + infra/repository.py:540-541` — implementer MUST pin the exact precedent location when fixing (search both, prefer the one with the matching `changed_by` audit pattern; document the choice in a code comment)

### P2 Task 2.10c — `tests/unit/tools/test_ens_db_repair_idempotent.py` (NEW — checklist item 3)

**Pattern:** NEW per checklist; run each repair SQL twice, assert net state change == 0 — pins R14 idempotent `DO$$`-only rule.

**Required assertions:**
- For each repair SQL in a fixture list: execute → row count snapshot → execute again → assert final row count == snapshot (net change = 0)
- Idempotency holds even across `BEGIN/COMMIT` boundaries

### P2 Task 2.10d — `tests/unit/tools/test_ens_db_repair_refuses_non_idempotent.py` (NEW — **normalized** from typo)

**Original medium plan text:** "asserts repair tool refuses non-idempotent DML and any `*.sql` filename input — migration-runner trap"

**Normalized test name:** `test_ens_db_repair_refuses_non_idempotent.py` (replaces typo `test_ens_db_repair_idempotent_doller.py`)

**Required assertions:**
- Non-idempotent DML (e.g. `INSERT INTO ... VALUES (random)`) is REFUSED at tool input validation
- Any input matching `*.sql` filename pattern is REFUSED (migration-runner trap per `daemon/migrations/runner.py:486-491`)
- Tool returns a structured refusal (error message naming the rule violated)

### P2 Task 2.10e — `tests/unit/tools/test_privileged_category_system_log.py` (NEW)

**Required assertions:**
- developer base + developer[v2] + wanderer LOSE `system-log` (resolved tool set excludes `ens_system_log_*`)
- worker KEEPS `system-log` only (resolved tool set includes `ens_system_log_*` but NEVER `ens_db_*`)
- watcher LOSES `system-log` (R-SR16 side-effect)
- **Enumerates base AND versioned metas** (architect §4.3 — standing guard for future `developer[v3]`-class additions)
- **LEADER ADJUDICATION #1 (2.7a):** `agents/_baby_template/meta.json` `tools.allow` contains ZERO entries from `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade", "system-log", "ens-db"})` (regression for the template-centralization-leak fix)
- **LEADER ADJUDICATION #2 (2.7b):** variant-tolerant grep over `agents/developer/`, `agents/developer[v2]/`, `agents/wanderer/` prompt files (`soul.md`, `rule.md`, `workflow.md`, `tools_note.md`) returns ZERO matches for `ens_system_log_*` / `system-log` / "log forensics" usage instructions (zero-count closure — enumerate-by-grep, not caller's item count). Worker's prompt files retain their existing references (no assertion on worker).
- **LEADER ADJUDICATION #6 (D20 env-agnostic):** ONE unit test asserting the `tools.deny` strip fires for `system_restart` across **mocked env values** (dev/demo/live) — `resolve_tool_filter` is env-agnostic BY DESIGN (the whole point of D20 resolution-level enforcement); call-time live refusal at `upgrade_tools.py:1458-1465` stays defense-in-depth, **untestable in CI without live-env fixture** — that acceptance is recorded (no live-env CI exists per leader adjudication #6; live = operator's prod daemon).

### P2 Task 2.10f — `tests/integration/test_maintenancer_spawn_resolves_tools.py` (NEW — CRITICAL)

**Required assertions:**
- maintenancer gets: `ens-db` + `system-log` + `system_upgrade` (non-`system_restart` subset — D20) + `knowledge` + `db` (D19 — external connections only, NOT `ensemble_prod`)
- **`system_restart` is EXCLUDED via `tools.deny` resolution-level strip at `resolve_tool_filter` time** (D20 enforcement)
- developer + developer[v2] get NEITHER `system-log` NOR `ens-db`
- worker gets `system-log` only

### P2 Task 2.10g — `tests/integration/test_maintenancer_upgrade_gate_refuses.py` (NEW)

**Required assertions:**
- A `system_upgrade` live request WITHOUT a user nonce echoes `factor_failures: user-confirmation-missing` (architect §5.3 — mirror ari test pattern)
- With valid nonce + valid user-origin row → request arms (verify via `upgrade_status` polling)

### P2 Task 2.10h — Dual-engine CI coverage (architect §7.5 — checklist item 2)

**Required:** all (a)-(g) tests above must pass on **BOTH SQLite-backed AND PG-backed test fixtures**. Use a pytest fixture parameter that runs each test against both engines.

**Acceptance:** all 8 test files pass; integration test on a freshly-restarted daemon confirms gate behavior + dual-engine coverage.

---

# W1-P3 — Local KB Authoring (worktree C)

**Owner:** developer C
**Branch:** `feature/maintenancer-agent-p3-kb` (or per giter convention)
**Target merge:** W1 (alongside P1 + P2; P3 has NO merge-order dependency on P1 or P2)

**File-disjointness contract (P3 only touches these):**
- `agents/maintenancer/knowledge/01-architecture-overview.md` (task 3.1)
- `agents/maintenancer/knowledge/02-jobs-missions-admission-state.md` (task 3.2)
- `agents/maintenancer/knowledge/03-log-forensics.md` (task 3.3)
- `agents/maintenancer/knowledge/04-known-traps.md` (task 3.4)
- `agents/maintenancer/knowledge/05-repair-runbooks.md` (task 3.5)
- `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md` (task 3.6)
- `agents/maintenancer/skills-template/kb-curator.md` (task 3.7)
- `tests/unit/test_maintenancer_kb_coverage.py` (task 3.8)

**P3 must NOT touch:** `agents/maintenancer/memory.md` (P1 owns the KB INDEX slot skeleton — P3 should NOT edit it; the INDEX stays as P1 wrote it).

## P3 Task 3.1 — `agents/maintenancer/knowledge/01-architecture-overview.md`

**Scope (~1.5k chars):**
- daemon manager facade
- graph + middleware slots
- repositories (sync engine bound to ~16)
- checkpoint adapter
- prompt loader (`daemon/loader.py` 11-section composition — see `daemon/loader.py:373-385`)
- registry singleton (import-time, `daemon/registry.py:1170-1175`)

**Header:**
```
# 01 — Architecture Overview
last-verified-against: v0.12.4
verify-against-source: pyproject.toml version (release tag, NOT rolling SHA — per D12/D17 + architect §6.3)
```

**Acceptance:** digest accurate against latest; cross-refs resolve.

## P3 Task 3.2 — `agents/maintenancer/knowledge/02-jobs-missions-admission-state.md`

**Scope (~1.5k chars):**
- 4-value `AdmissionState` (QUEUED/ACTIVE/DONE/DEAD)
- legacy `JobStatus` shim
- receipts vs stateful proxies
- `daemon/services/job_state_machine.py` transitions (retries, dead-lettering, replay, cancellation, post-commit re-arming)
- lock-first concurrency

**Header:** `last-verified-against: v0.12.4`

**Acceptance:** digest cites `daemon/services/job_state_machine.py`.

## P3 Task 3.3 — `agents/maintenancer/knowledge/03-log-forensics.md`

**Scope (~1k chars):**
- **Time-bracket forensics rule** (line numbers are NOT chronological; interleaved append regions)
- `ens_system_log_*` tool usage (`list` / `read` / `search` / `tail`)
- rotation policy
- redaction caveat (API keys, tokens, Bearer → `[REDACTED]`)
- SSRF / size cap awareness

**Header:** `last-verified-against: v0.12.4`

**Acceptance:** digest cites the 2026-09-04 log-trap critical note; rule is operational.

## P3 Task 3.4 — `agents/maintenancer/knowledge/04-known-traps.md`

**Scope (~2k chars):**
- (i) stale SQLite relic at `data/instances.db` (prod = `ensemble_prod` PG only)
- (ii) migrations runner is SQLite-only by design (`daemon/migrations/runner.py:486-491`) — PG schema uses `create_all + _ensure_postgres_columns`
- (iii) idempotent `DO$$ DROP NOT NULL` recipe (`daemon/manager.py:4996-5012`)
- (iv) pause-first then quiesce convention
- (v) auto-promote kill-switch default ON (merge `feb5e915`)
- (vi) sentinel `''` bridge for `report_injections.content` NOT-NULL drift (4th defect on this table DDL must eliminate drift)

**Header:** `last-verified-against: v0.12.4`

**Acceptance:** digest enumerates each trap with the fix recipe and a file:line anchor.

## P3 Task 3.5 — `agents/maintenancer/knowledge/05-repair-runbooks.md`

**Scope (~2k chars):**
- pause-first quiesce sequence
- idempotent DROP NOT NULL recipe
- CHECKPOINT-aware migration recipe
- `MaintenanceJob` registry usage (`daemon/services/maintenance.py:68`, 15-min interval)
- `StaleTaskRecovery` (`daemon/services/stale_task_recovery.py:42`)
- orphan ACTIVE job sweep (`daemon/routers/api.py:517-526`, ≤20min)

**Header:** `last-verified-against: v0.12.4`

**Acceptance:** each runbook has trigger + steps + verification.

## P3 Task 3.6 — `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md`

**Scope (~1.5k chars):**
- pause-first quiesce
- journal sweep
- live gate via 3-factor (`daemon/tools/upgrade_tools.py:1877-2036`)
- `adopt_stale_txn`
- atomic flip (`scripts/upgrade/lib.sh:~1126`)
- DR-1 frozen-binary preflight (`daemon/__main__.py:104`)
- cycle ledger (`scripts/upgrade/ledger_check.py`)
- restart-required kill-switch activation recipe (per `kv-ambient-awareness-fix` rollout)

**Header:** `last-verified-against: v0.12.4`

**Acceptance:** digest cites the upgrade pipeline blueprint.

## P3 Task 3.7 — `agents/maintenancer/skills-template/kb-curator.md` (TIERED design)

**File:** `agents/maintenancer/skills-template/kb-curator.md` (NEW)

**Scope:** TIERED design per architect §6.1+§6.2. The original "startup recording via experience()" capability is REMOVED (architecturally impossible — skills are static prompt text; no startup execution hook exists).

**Three responsibilities to instruct the LLM on:**
- **(a) Index responsibility:** the KB INDEX (one-line-per-doc triggers + verification discipline) is authored in `memory.md` (task 1.7 slot) — guaranteed-on via the loader
- **(b) Content path responsibility:** full KB doc retrieval via filesystem `read_file` on demand when a specific doc is needed
- **(c) First-turn RAG mirror duty:** on the agent's first turn, override `no_force_explore` and call `experience(text=<each KB doc>)` to mirror the KB into RAG for cross-session recall. The first-turn duty is a workflow rule (not a skill capability) — pin in `workflow.md` and enforce via a grep test that asserts the directive is present.

**Frontmatter:** `version: 1.0.0`; manifest version must match.

**Acceptance:** skill body has one example per responsibility; frontmatter version matches manifest; no startup-hook claims.

## P3 Task 3.8 — `tests/unit/test_maintenancer_kb_coverage.py` (NEW — checklist items 4 + 6)

**Required assertions:**
- Enumerates the 6 KB docs and asserts each contains its trap reference by string:
  - `time-bracket`
  - `data/instances.db`
  - `runner.py:486-491`
  - `feb5e915`
  - `pause-first`
  - `DROP NOT NULL`
  - `report_injections.content`
  - `sentinel`
  - `3-factor`
  - `adopt_stale_txn`
- **Release-tag pinning (architect §6.3 — checklist item 6):** each doc's `last-verified-against` header equals the **merge-base release tag** at KB-file-change time (NOT `git rev-parse HEAD` — rolling SHA is brittle in CI; this repo merges 20+/day)
- **KB-duplication assertion (checklist item 6):** asserts the KB INDEX in `memory.md` does NOT duplicate KB content (only lists triggers + verification pointers — load-bearing per architect §6.2 Tier 1)

**Acceptance:** test passes; runtime divergence is warn-only (refuse-to-cite is unenforceable against an LLM per architect §6.3).

---

# W2-P4 — Skills + Workers (worktree A or new)

**Owner:** developer A (returns after W3) or a new developer
**Target merge:** AFTER all of W1 (P1+P2+P3 merged)

## P4 Task 4.1 — Finalize skill list

**Decision (medium plan):**

| Skill | One-line purpose | Cross-refs | Guardrail boundary |
|-------|------------------|------------|---------------------|
| `log-forensics` | Time-bracket log forensics + redaction-aware reads | §3 KB | read-only; no write path |
| `job-mission-repair` | Diagnose + repair job/task/mission state | §2 KB | uses `ens_db_repair_execute` (guarded) |
| `ens-db-repair` | **Guarded execution** — idempotent DO$$-only repair + audit + dry-run | §4 + §5 KB | `ens_db_repair_execute` only (3 gates); NEVER consultation output |
| `restart-upgrade-ops` | Pause-first quiesce + dry-run upgrade prep | §6 KB | dry-run + prep; live upgrade arms only via 3-factor gate |
| `bug-advisory` | **Read-only consultation** — analyze bug, diagnose, recommend fix sketch | n/a | NEVER executes repairs; output is a recommendation report only |
| `health-check` | MaintenanceJob registry + StaleTaskRecovery sweep | §5 KB | read-only sweep + diagnostic |
| `kb-curator` | KB INDEX awareness + filesystem reads + first-turn RAG mirror | §6.2 | read-only; `experience()` writes only to RAG |

**LEADER ADJUDICATION #3 boundary sentence (applied to both `bug-advisory` and `ens-db-repair` in P4 task 4.2 skill files):**

- `bug-advisory` skill body MUST include: "Advisory is read-only consultation — I diagnose and recommend, never execute repairs. Execution is the `ens-db-repair` skill's job."
- `ens-db-repair` skill body MUST include: "This is a guarded-execution skill — I follow the 3 gates (confirm, audit, dry-run). Consultation output is the `bug-advisory` skill's job."

**Rationale (leader adjudication #3):** user explicitly wants the advisory role; execution/consultation postures differ in guardrails. Advisory = read-only; `ens-db-repair` = guarded execution. Keep distinct.

**Acceptance:** 7 skills; each has 1-line purpose documented in `memory.md`; both `bug-advisory` and `ens-db-repair` skill files carry the boundary sentence above.

## P4 Task 4.2 — Author 7 skills-template files

**Files (NEW, one per skill):**
- `agents/maintenancer/skills-template/log-forensics.md`
- `agents/maintenancer/skills-template/job-mission-repair.md`
- `agents/maintenancer/skills-template/ens-db-repair.md`
- `agents/maintenancer/skills-template/restart-upgrade-ops.md`
- `agents/maintenancer/skills-template/bug-advisory.md`
- `agents/maintenancer/skills-template/health-check.md`
- `agents/maintenancer/skills-template/kb-curator.md` (P3 task 3.7)

**Pattern per file:** mirror `agents/tester/skills-template/test-strategy.md` — frontmatter `version: 1.0.0`, contract (what worker does on `load_skill`), focus areas, mandatory output format.

**Compliance:**
- Each skill ≤2k chars
- Frontmatter version matches `skill-set.yaml` version (writing guide §6)
- NO cross-references to forbidden tokens per writing guide §1 (no `meta.json`, `daemon/`, `_tool_registry`, `tools.allow` in prose)
- KB cross-refs use section-name form (writing guide §3 convention v2)

**Acceptance:** all 7 skills present; frontmatter matches manifest.

## P4 Task 4.3 — Update `agents/maintenancer/skill-set.yaml`

**File:** `agents/maintenancer/skill-set.yaml` (P1 wrote empty placeholder; P4 populates)

**Edit:** add 7 entries — one per skill. Set `auto_load: true` for `kb-curator` (Tier 3 first-turn RAG duty). Others are worker-loaded via `load_skill`.

**Acceptance:** yaml version == frontmatter version per skill (writing guide §6).

## P4 Task 4.4 — Refine `agents/maintenancer/rule.md` Cardinal #3 / #4 wording

**File:** `agents/maintenancer/rule.md` (P1 wrote placeholder; P4 refines)

**Note:** P1 author wrote a Cardinal #4 placeholder. **P4 task 4.4 owns the FINAL wording** per approver directive ("Cardinal #4 wording (owned by task 4.4) — leave as-is in P1").

**Required refinements (LEADER ADJUDICATION #7 — D8's expanded form is AUTHORITATIVE):**

- Cardinal #3 — refine END TURN wording per writing guide §7
- Cardinal #4 — **AUTHORITATIVE FORM per `decisions.md` D8** (transcribe verbatim — the medium plan's tight pre-D20 form is DEPRECATED):

> **Never run `system_restart` — `system_restart` is in `tools.deny` (resolution-level strip at `resolve_tool_filter` time per approver-fix pass; see D20); never bypass the 3-factor nonce gate on `system_upgrade`; relay any user nonce verbatim without agent-side fabrication (per OPEN A).**

**Reviewer check:** W2 output of `agents/maintenancer/rule.md` is verified against `decisions.md` D8 verbatim — wording match (modulo punctuation/whitespace) is the gate.

**Cardinals stay ≤7; no skill names leaked into cardinals (use role descriptions).**



**Cardinals stay ≤7; no skill names leaked into cardinals (use role descriptions).**

## P4 Task 4.5 — Finalize `agents/maintenancer/meta.json` team_members

**File:** `agents/maintenancer/meta.json`

**Edit:** confirm `team_members: ["explorer", "worker", "coder"]` (P1 task 1.2 already set this; P4 confirms + verifies no drift).

**Worker holds `system-log` only — NEVER `ens-db` (DB write path closed at worker layer; pin in 2.10e exclusivity test).**

**Acceptance:** meta.json valid; worker holds `system-log` only; `ens-db` not on worker's allow list.

## P4 Task 4.6 — Refine `agents/maintenancer/workflow.md` dispatcher pattern

**File:** `agents/maintenancer/workflow.md` (P1 wrote skeleton; P4 refines)

**Required final content:**
- `send_message` with `load_skill="<one skill>"` (one skill per spawn)
- Max 3 concurrent workers
- Fan-in escape valve (1 re-dispatch cap + `[incomplete]` + `### Gaps`)
- Report-sanity scrutiny conditioned on `[REPORT SANITY: …]` marker
- First-turn RAG mirror duty (workflow rule, not skill capability — `workflow.md` carries the directive per architect §6.1)
- `no_force_explore: true` override for the one-time first-turn mirror

**Acceptance:** escape valve ladder present; scrutiny rule stated once (writing guide §7); cross-refs use section-name form.

## P4 Task 4.7 — Tests (4 NEW test files)

| File | Assertion |
|------|-----------|
| `tests/unit/test_maintenancer_skill_versions_consistent.py` | frontmatter `version` == `skill-set.yaml` version for all 7 skills |
| `tests/unit/test_maintenancer_report_scrutiny.py` | workflow.md contains `[REPORT SANITY: …]` marker pattern (modeled on `tests/unit/test_report_integrity_prompts.py`) |
| `tests/unit/test_maintenancer_fan_in_valve.py` | workflow.md contains the fan-in escape valve ladder (stuck confirm → 1 re-dispatch → `[incomplete]` + `### Gaps`) AND the max-re-dispatch cap |
| `tests/unit/test_maintenancer_team_members_within_team.py` | fallback references in skills stay within `team_members` per writing guide §8 |

**Acceptance:** all 4 tests pass.

---

# W3-P5 — Docs + Compliance + Rollout (worktree A or new)

**Owner:** developer A (returns after W3) or a new developer
**Target merge:** AFTER W2 (P4 merged)
**Followed by:** WAVE 3 single deploy window — pause-first quiesce → restart daemon → run all pin tests → smoke-spawn

## P5 Task 5.1 — Author `agents/maintenancer/README.md`

**File:** `agents/maintenancer/README.md` (NEW)

**Required content:**
- Purpose (humans-facing summary)
- When leader dispatches (per OPEN A/B/C/D resolution)
- What it can/can't do
- Restart-required note (one-time Wave 3 deploy)
- KB index (six docs by name — enumerate, NOT deep-link per checklist #6 reasoning)
- Skill list (seven by name — enumerate, NOT deep-link)
- Rollout link

**Acceptance:** README accurate and concise; KB index matches `knowledge/` directory; skill list matches `skills-template/` directory.

## P5 Task 5.2 — Run prompt-guide §10 checklist

**Action:** run `docs/agent-prompt-writing-guide.md` §10 pre-commit checklist against `agents/maintenancer/`; fix every violation.

**Acceptance:** checklist sign-off recorded in PR; pin test from 1.11 passes.

## P5 Task 5.3 — Update leader routing line

**File:** `agents/leader/soul.md` (or the canonical-home prompt surface per writing guide §2 canonical-home rule)

**Edit:** add routing line — section-name ref per convention v2 (writing guide §3). Example wording:

> "For ensemble system maintenance (log forensics, `ensemble_prod` repair preparation, restart/upgrade preparation), dispatch to `maintenancer`."

**Acceptance:** leader prompt has the routing line; convention v2 clean (no path tokens).

## P5 Task 5.4 — Author rollout runbook

**File:** `agents/maintenancer/README.md` §Rollout OR sibling `ROLLOUT.md` (NEW)

**Required structure (3-wave procedure per architect §2.3 + checklist items 5 + 6):**

**W1 (P1 ∥ P2 ∥ P3, file-disjoint, NO in-wave restart):**
1. Land P1 (agent scaffolding + leader team_members) — **P1-before-P2 merge-order invariant**
2. Land P2 (tool layer + exclusivity + frozenset + same-PR dual-pin updates)
3. Land P3 (KB docs + kb-curator skill refactor + KB INDEX in memory.md)
4. Any crash-restart mid-W1 lands coherent state

**W2 (P4, NO restart):**
5. Land P4 (skills + team_members finalization + dispatcher pins)

**W3 (P5 + ONE restart window):**
6. Land P5 (docs + §10 checklist + leader routing line + integration test + runbook)
7. **Pause-first quiesce** any instance whose work overlaps the new agent. Verify with explicit command:
   ```bash
   curl -s -X POST http://localhost:8079/api/instances/<id>/pause
   ```
   Confirm `is_paused=true` in `instances` row (per pause-first convention; `daemon/routers/instances.py:647-669`). **(checklist item 6)**
8. **Pool-config pre-flight on disposable PG** (checklist item 5) — verify `pool_recycle × pool_pre_ping × reset-on-return` interaction on a disposable PG BEFORE W3 restart (architect §3.1, §10)
9. **Restart the daemon** — required because `_registry` is an import-time singleton (`daemon/registry.py:1170-1175`); `_registry.discover()` runs once
10. Run all pin tests — Phase 1.11, Phase 2.10 (a)-(h), Phase 3.8, Phase 4.7, integration test 5.5
11. **Smoke-spawn** — leader sends a small maintenance prompt; verify KB INDEX + filesystem KB read + first-turn RAG mirror + skill dispatch work; verify `system-log` / `ens-db` / `system_upgrade` (non-`system_restart` subset per D20) / `knowledge` / `db` (external only) resolved
12. Monitor first 24h: watch `ens_system_log_tail` for denials + `upgrade_promote_refusal` journal events (architect §5.3)
13. Worker break-glass verified — incident-time raw-bash fallback documented; leader re-spawns on maintenancer ERROR

**Rollback (complete per architect §2.4):**

- **Pre-P2 (anytime in W1):** revert P1/P2/P3 PRs as appropriate
- **Post-P2:** revert frozenset + CATEGORY_MODULES + DYNAMIC_TOOL_NAMES/KNOWN_TOOL_NAMES regen + de-scopes + `agents/maintenancer/meta.json` allow-list additions + **`tools.deny` entries (remove `"system_restart"` per D20)** + `create_ens_db_tools` wiring + same-PR dual pin updates
- **Executed repairs have NO structural rollback** — recovery via snapshot-before-repair + idempotent `DO$$` forward-fix from `repair_log.created_by`

**Acceptance:** runbook has step-by-step with verification commands; wave structure is explicit; rollback section completes architect §2.4 list.

## P5 Task 5.5 — `tests/integration/test_maintenancer_end_to_end.py`

**File:** `tests/integration/test_maintenancer_end_to_end.py` (NEW)

**Required flow:**
- leader spawns maintenancer
- maintenancer loads KB INDEX from `memory.md` (Tier 1 load-bearing)
- filesystem-reads a sample KB doc on demand (Tier 2 content path)
- first-turn RAG mirror runs `experience()` on each doc (Tier 3 best-effort)
- spawns worker with `load_skill="log-forensics"`
- worker returns evidence
- report-sanity scrutiny passes
- worker reports back
- assert no tool-resolution errors
- assert the final report carries evidence
- assert the 3-factor gate refuses a `system_upgrade` request missing the nonce (architect §5.3 — mirrors ari pattern)

**Acceptance:** integration test passes on a fully-restarted daemon (single deploy window per wave structure).

---

## Leader Adjudications (pre-W1)

Per leader adjudication, the 7 gaps previously surfaced here are RESOLVED. Each adjudication records the **decision** + a **one-line rationale** + the **affected task(s)** for fold-in tracking. Task-level spec changes are applied above in their respective sections (P1/P2/P4/P5).

### A1 — `_baby_template` cleanliness → FIX IT (P2 scope)

- **Decision:** Remove `system-log` from `agents/_baby_template/`'s illustrative `tools.allow`. The template's example allow-list must not contain ANY `PRIVILEGED_TOOL_CATEGORIES` member.
- **Rationale:** NOT just hygiene — privileged categories are reachable ONLY via explicit allow, so a copied template entry would GRANT logs to every future agent (centralization leak).
- **Affected:** new **P2 Task 2.7a** (added above); **P2 Task 2.10e** extended with template regression assertion.

### A2 — Developer post-de-scope audit → YES, new P2 sub-task

- **Decision:** Variant-tolerant grep (per repo convention: BOTH spaced and unspaced forms, enumerate-by-grep closure) of `agents/developer/`, `agents/developer[v2]/`, `agents/wanderer/` prompt files (`soul.md` / `rule.md` / `workflow.md` / `tools_note.md`) for `ens_system_log_*` / `system-log` usage instructions; remove or reword to "delegate ensemble log forensics to maintenancer". Worker's references STAY (retains break-glass access per OPEN B Option 1).
- **Rationale:** developer currently may reference log-forensics directly; centralization intent requires the delegation pattern; worker's break-glass is preserved.
- **Affected:** new **P2 Task 2.7b** (12 prompt files reworded); **P2 Task 2.10e** extended with zero-count closure grep assertion (worker prompt files NOT asserted).

### A3 — `bug-advisory` vs `ens-db-repair` → KEEP DISTINCT

- **Decision:** Advisory = read-only consultation (diagnose + recommend, NEVER executes repairs); `ens-db-repair` = guarded execution. Sharpen BOTH skill descriptions (W2-P4 task 4.2) with the boundary sentence.
- **Rationale:** user explicitly wants the advisory role; execution/consultation postures differ in guardrails.
- **Affected:** **P4 Task 4.1** table extended with `Guardrail boundary` column + boundary sentence block; **P4 Task 4.2** skill files (bug-advisory + ens-db-repair) carry the boundary sentence.

### A4 — `repair_log` table → SQLAlchemy model + `create_all`

- **Decision:** **SQLAlchemy model + `create_all`**, NO hand-written migration, NO `_ensure_postgres_columns` entry.
- **Rationale:** new TABLE (not column-adds on existing table) → `create_all` creates it on BOTH engines (SQLite + PG) even against existing DBs (existence-checked); `_ensure_postgres_columns` exists for column-adds; migrations runner is SQLite-only by design.
- **Affected:** **P2 Task 2.1** `repair_log` spec rewritten; **P2 Task 2.10h** dual-engine coverage verifies pickup.

### A5 — Repair-pool saturation → bounded 10s wait then fast-fail

- **Decision:** `pool_timeout=10s` aligned with `lock_timeout=10s` initial; on timeout the tool returns a clear "repair pool saturated — a repair is in flight; retry when it completes" error.
- **Rationale:** repairs are rare, human-initiated, idempotent — SERIALIZED is the desired semantics; no queueing pileup. The kill-switch already covers full disable.
- **Affected:** **P2 Task 2.1** pool spec extended with `pool_timeout=10s` + saturated-pool error message.

### A6 — D20 live-env verification → dev/demo pin IS sufficient

- **Decision:** Dev/demo pin is sufficient. Add ONE unit test asserting the deny-strip fires across mocked env values (dev/demo/live); the call-time live refusal stays defense-in-depth, untestable in CI — that acceptance is recorded.
- **Rationale:** no live-env CI exists (live = operator's prod daemon); D20's deny-strip is env-agnostic BY DESIGN (resolution-level) — that's the whole point. The call-time live refusal at `upgrade_tools.py:1458-1465` is defense-in-depth and is not unit-testable in CI without live-env fixture.
- **Affected:** **P2 Task 2.10e** extended with env-agnostic mocked-env unit test + the recorded acceptance.

### A7 — Cardinal #4 wording → D8's expanded form is AUTHORITATIVE

- **Decision:** `decisions.md` D8's expanded form is AUTHORITATIVE. The medium plan's tight pre-D20 form is DEPRECATED. Reviewer verifies W2 output of `rule.md` against D8 verbatim.
- **Rationale:** D8 cites the D20 enforcement point AND the gate, giving the agent both the negative constraint AND the positive duty; the medium plan's pre-D20 form predates the deny-list correction.
- **Affected:** **P4 Task 4.4** Cardinal #4 marked AUTHORITATIVE per D8; reviewer check is the gate.

---

## Acceptance Summary — what "done" looks like per wave

**W1 done when:**
- P1 PR merged on `feature/maintenancer-agent` (11 tasks: 1.1-1.11)
- P2 PR merged on `feature/maintenancer-agent` after P1 (12 tasks: 2.1-2.10 + 2.7a + 2.7b — adjudication-driven additions)
- P3 PR merged on `feature/maintenancer-agent` (independent of P1/P2 merge order; 8 tasks: 3.1-3.8)
- All pin tests green in CI:
  - P1 (1.11)
  - P2 (2.10a-2.10h; 2.10e extended per adjudication #1 + #2 + #6)
  - P3 (3.8)
- Two exact-equality pins in P2 (`tests/unit/tools/test_upgrade_registration.py:100` + `tests/unit/tools/test_attestation_registration.py:154`) updated in same P2 PR
- **`agents/_baby_template/meta.json` regression-test green** (adjudication #1)
- **Variant-tolerant grep over de-scoped agents returns ZERO matches** (adjudication #2)

**W2 done when:**
- P4 PR merged on `feature/maintenancer-agent` (7 tasks: 4.1-4.7)
- All P4 pin tests (4.7) green in CI
- **`bug-advisory` and `ens-db-repair` skill files carry the boundary sentence** (adjudication #3)
- **`rule.md` Cardinal #4 matches `decisions.md` D8 verbatim** (adjudication #7 — reviewer gate)

**W3 done when:**
- P5 PR merged on `feature/maintenancer-agent` (5 tasks: 5.1-5.5)
- **Pool-config pre-flight on disposable PG passed** (checklist item 5 + adjudication #5)
- **`repair_log` table created on both engines via `create_all`** (adjudication #4 — verify dual-engine CI)
- **D20 env-agnostic unit test green** (adjudication #6)
- Single deploy window executed: pause-first quiesce verified via explicit `curl` (checklist item 6) → restart daemon → all pin tests green on freshly-restarted daemon
- Smoke-spawn from leader to maintenancer succeeds
- First 24h monitoring baseline captured

**Maintenance-worker split trigger threshold** (deferred per architect §9) — define the usage-evidence threshold (e.g. N log-touching worker dispatches/month by non-maintenancer dispatchers) that would justify creating a dedicated `maintenance-worker` agent.

---

End of detail plan.
