# Phase 5b Blueprinter Integration Testing

**Date:** 2026-08-03
**Branch:** `feature/blueprint-evolution` (Phase 5a committed at `ebba780f`)
**Tester Instances:** 2 workers (1 static analysis + 1 registry test)
**Plan Ref:** `.agents/shared/planning/project-blueprint/evolution-phases-detailed.md`

---

## Summary

| Metric | Value |
|--------|-------|
| Registry test | ✅ 100/100 PASS |
| Static checks passed | 8/12 |
| 🔴 Critical findings | **4** (tool surface gaps) |
| 🟡 Warnings | **4** (rule count, JSON schema, skill output consistency, stage/publish mismatch) |
| Overall Status | ❌ **NOT READY for production** — 4 critical tool surface gaps must be resolved before the blueprinter agent can execute the incremental workflow |

---

## Item 6: Registry Test ✅

| Pack | Tests | Result | Runtime |
|------|-------|--------|---------|
| `blueprint_registry_unit_test` | 100/100 | ✅ PASS | 2s |

The blueprinter agent definition (meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml) does NOT break the full agent registry discovery suite. Agent discovery is clean.

---

## Items 1-2: Seeding + Prompt Validation ✅ (with 1 warning)

### ✅ Passing

| Check | Status |
|-------|--------|
| 4 skill files exist in skills-template/ | ✅ All present |
| skill-set.yaml valid, references all 4 skills | ✅ Valid YAML |
| Seeder correctly maps manifest → template path | ✅ Wired at `skill_seed_service.py:323-327` |
| meta.json valid JSON, all required fields correct | ✅ llm_model="balanced", skill_injection=true, blueprint_inactive=true, team_members=["worker"] |
| workflow.md references both workflows (rebuild + incremental) | ✅ |
| soul.md mentions two-workflow architecture | ✅ |
| tools_note.md references trigger_queries | ✅ |
| 5 existing blueprint tools match between prompts and backend | ✅ search/get/list/create/update |

### 🟡 Warning: rule.md has 12 cardinal rules (exceeds ≤7 convention)

`agents/blueprinter/rule.md` contains 12 numbered cardinal rules, which exceeds the 7-rule convention used by other v2 agents (developer, planner, tester). Rules 8-12 should be reclassified as guidelines or consolidated.

---

## Item 3: Tool Name Reconciliation 🔴 CRITICAL

### The Problem

The Phase 5a prompts reference **4 operations that are NOT exposed as agent-facing tools**:

| Prompt reference | Backend location | Agent tool? | Severity |
|-----------------|------------------|-------------|----------|
| `claim_batch` | `pending_repository.py:108` (repository method) | 🔴 **NO** | Critical |
| `get_pending_records` | `pending_repository.py:308` (repository method) | 🔴 **NO** | Critical |
| `acknowledge_batch` | `pending_repository.py:202` (repository method) | 🔴 **NO** | Critical |
| Disable write | `blueprint_write_service.py:540` (service method) | 🔴 **NO** | Critical |

The suspected names (`blueprint_claim_pending`, `blueprint_acknowledge_pending`, `blueprint_get_pending_records`) do **not appear anywhere** in `agents/blueprinter/`. The prompts use the unprefixed repository method names (`claim_batch`, `acknowledge_batch`, etc.) directly — but these are internal repository methods, not callable LangChain tools.

### Current Tool Surface (5 tools — all match)

| Tool | Exposed | Prompts reference | Match? |
|------|---------|-------------------|--------|
| `blueprint_search` | ✅ | ✅ | 🟢 YES |
| `blueprint_get` | ✅ | ✅ | 🟢 YES |
| `blueprint_list` | ✅ | ✅ | 🟢 YES |
| `blueprint_create` | ✅ | ✅ | 🟢 YES |
| `blueprint_update` | ✅ | ✅ | 🟢 YES |

### Impact

- **Rebuild workflow:** ✅ Works with current 5 tools (search → explore → create/update)
- **Incremental workflow:** 🔴 **BROKEN** — requires claim_batch/get_pending_records/acknowledge_batch/disable, none of which are agent-callable

### Recommended Resolution (choose one)

**Option A — Add agent-facing tools** (recommended):
- `blueprint_claim_pending` → wraps `pending_repo.claim_batch()`
- `blueprint_get_pending_records` → wraps `pending_repo.get_pending_records()`
- `blueprint_acknowledge_pending` → wraps `pending_repo.acknowledge_batch()`
- `blueprint_disable` → wraps `write_service.disable_blueprint()`

**Option B — Single orchestration tool:**
- One tool that performs the full pending-queue lifecycle internally

---

## Item 4: Worker Report Schema 🟡 Warning

- **No JSON schema exists** — the worker report format is Markdown, not JSON
- 3 skills (explore-for-rebuild, explore-for-incremental, build-blueprint) use a consistent Markdown Worker Report with fields: Summary, Areas Found, Blueprint Recommendations/Payload, File References, Confidence
- `decide-changes` uses a separate "Decision Set" format (Actions, Priority Order, Heartbeat Sent, Model Tier Used)
- **Inconsistency:** if a runtime parser expects JSON or expects all 4 skills to emit the same schema, the contract is absent

---

## Item 5: Compare/Stage/Publish Pattern 🟡 Warning

- **Terminology is consistent** across all prompt files: Compare → Stage (draft) → Publish (published)
- 🔴 **But not directly callable** through agent tools:
  - No `stage` tool exists
  - No `publish` tool exists
  - `blueprint_update` does NOT expose `status` parameter at the tool level (service accepts it internally, but the tool doesn't pass it through)
  - `blueprint_create` defaults to `status="published"` — there's no way to create a draft
- The backend HAS the infrastructure (`BlueprintWriteService.update_blueprint()` accepts status, `plan_publication()`, `execute_save_plan()`), but none is exposed through the tool surface

---

## Findings Summary

### 🔴 Critical (must fix before production)

1. **Pending queue tools not exposed** — `claim_batch`, `get_pending_records`, `acknowledge_batch` are repository methods, not agent tools. Incremental workflow is broken.
2. **Disable tool not exposed** — prompts reference disable operations but no `blueprint_disable` tool exists.
3. **Stage/publish tools missing** — prompts require draft staging, but `blueprint_update` doesn't expose `status` and no stage/publish tools exist.
4. **No draft creation path** — `blueprint_create` defaults to `status="published"`, no way to create a draft.

### 🟡 Warning (should fix)

1. `rule.md` has 12 cardinal rules (convention is ≤7).
2. No JSON Worker Report schema (format is Markdown).
3. Skills don't all use same output schema (decide-changes differs).
4. Compare/stage/publish pattern is documented but not tool-callable.

### 🟢 Passing

1. All 4 skill files exist and are correctly seeded.
2. skill-set.yaml is valid.
3. meta.json has all correct fields.
4. Both workflows documented.
5. soul.md describes two-workflow architecture.
6. tools_note.md references trigger_queries.
7. 5 existing tools match prompts exactly.
8. Registry test 100/100 PASS (no discovery breakage).

---

## Overall Status

- **Registry:** ✅ PASS (100/100)
- **Seeding:** ✅ PASS (4 skills correctly wired)
- **Prompt Validation:** ✅ PASS (with 1 warning on rule count)
- **Tool Name Reconciliation:** 🔴 **4 CRITICAL GAPS** — incremental workflow is not callable
- **Worker Report Schema:** 🟡 Markdown, not JSON; skills not fully consistent
- **Compare/Stage/Publish:** 🟡 Terminology consistent but not tool-callable
- **Testing Complete:** ❌ **NOT READY** — 4 critical tool surface gaps must be resolved before the blueprinter agent can execute the incremental workflow
