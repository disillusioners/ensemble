# Phase B: Base Copy (5 base agents := v2 byte-identical)

## Objective

Replace the **content** (not the dir) of these 5 base agent directories with byte-identical copies of their v2 counterparts: `approver`, `developer`, `planner`, `reviewer`, `tidier`. After this phase, every base dir contains EXACTLY the v2 content (same files, same bytes, same permissions, same mtime preserved where possible); the v2 trees stay UNTOUCHED; the other 29 base agent dirs + 4 infra dirs are UNCHANGED. Acceptance: a fresh user on `approver`/`developer`/`planner`/`reviewer`/`tidier` (via the default untagged dir) gets the same agent as a user on `approver[v2]` etc.

## Scope

### In Scope

1. Capture v2 before-snapshot manifests (5 dirs, one per pair) **before any write** — D4 ZERO-TOUCH proof, plus per-file SHA for the post-copy diff.
2. For each of the 5 pairs, perform a **surgical** content replacement (DO NOT delete the base dir):
   - Remove base-only files (the inventory below enumerates per-dir).
   - Copy v2 files into base dir (preserving permissions via `cp -pR` style; the goal is byte-identical to v2, so the v2 file is the source of truth).
3. After-copy verification: per-file sha256 base vs v2 — diff MUST be empty for all 5 pairs.
4. Capture v2 after-snapshot manifests — diff vs before-snapshot MUST be empty (D4 ZERO-TOUCH proof).
5. Update the meta-pin tests enumerated below (they hard-pin pre-copy base values; copying the new content makes their pins wrong).

### Out of Scope

- Base dir renaming or deletion (binding D1 + Risk #2 — `registry.py:918,1207-1225,730-741` uses base dir name as primary key for `get()`/`get_resolved()`/`validate_agent_id`; no per-agent DB rows; `config.yaml` agents.directory is for routing only). Surgical ops satisfy content-replacement without touching the dir entry.
- The other 29 base agents (`architect`, `ari`, …, `worker`) — UNTOUCHED. Verified by `git status --porcelain -- 'agents/!(*[v2]*)'` showing only the 5 targeted dirs changed.
- The 4 infra dirs (`_baby_template`, `_inner_soul`, `_mother`, `_prompt_system`) — UNTOUCHED. These are either SKIP_DIRS in `daemon/registry.py:19-25` or used as system:true templates.
- v2 paths under `agents/*[v2]/` — PROOF of untouched-ness is a DoD item, not a step (D4).
- Test pack renames or opencode wiring excision — Phase C.
- Documentation references — Phase D.
- Whole-mission evidence pack — Phase E (consumes B's manifests).
- Live system contact — D8 (no daemon restart, no DB writes, no promote, no install-dir touches).

### Justification for scope boundary

- D1 mandate ("exactly the 5 agents with v2 counterparts… NO base dir is ever deleted or renamed") defines the surgical scope precisely.
- Per-pair inventory is enumerated explicitly because the "remove + add" sets are per-pair and per-hand-waving would leak bugs.
- D2-D4 mandate byte-identical copies + v2 untouched + archive-before-overwrite (Phase A handles archive). Phase B is pure content replacement under those constraints.

## Decision Points (resolved in this plan, justifications cited)

| # | Decision | Choice | Justification |
|---|----------|--------|---------------|
| **D-B1** | Base-only file disposition: per-pair `rm` + per-pair `cp -R` (surgical), OR `rm -rf base` + `cp -R v2 base` (full rebuild) | **Surgical** | D1 binding constraint "NO base dir is ever deleted or renamed" + Risk #2 (registry paths tied to dir names). Surgical ops minimize git churn (only differing files appear in `git status`), keep base-dir names stable for any tooling that relies on dir existence, and produce identical end state. |
| **D-B2** | Copy transport | `cp -pR v2_dir/. base_dir/` (preserves permissions + mtime) plus per-file `shasum -a 256` check | `cp -p` = preserve mode/ownership/timestamps; `-R` = recursive; trailing `/.` = copy CONTENTS not the dir itself. After copy, per-file SHA proves byte-identical-to-v2 regardless of cp metadata deltas. |
| **D-B3** | Meta-pin test update timing | **Inline with Phase B (NOT deferred to a follow-up PR)** | Tests that pin `"version": "1.0.0"` will FAIL after Phase B because base meta.json now reads `"version": "2.0.0"`. The mission's PR must include test pin updates to ship green CI; this plan enumerates the test files and the values to update. |
| **D-B4** | Single-prong copy or staged (per-agent) | **Per-agent staged** | Avoids the risk that one failed copy leaves 4 done + 1 broken. Each per-agent step is independently verifiable. Phase B DoD requires ALL 5 done, but execution can stop on the first failure with a clean rollback (see Phase Rollback). |
| **D-B5** | Inventory verification | **Re-verify per-dir file lists at execution start** (the inventory below was captured 2026-10-13) | The worktree is current, but a re-`find` at execution time costs <1s and catches any drift introduced between plan and execution. |

## File Inventory (per-pair)

> Inventory verified 2026-10-13 in worktree via `find agents/<dir> -type f | sort`. Re-verify at execution start.

### Pair 1 — approver

| Action | Path | Notes |
|--------|------|-------|
| REMOVE | `agents/approver/memory.md` | Base-only; absent in v2 |
| REPLACE | `agents/approver/meta.json` | New content per v2 (version="2.0.0"; see test pin files) |
| REPLACE | `agents/approver/rule.md` | New content per v2 |
| REPLACE | `agents/approver/soul.md` | New content per v2 |
| REPLACE | `agents/approver/tools_note.md` | New content per v2 |
| REPLACE | `agents/approver/workflow.md` | New content per v2 |
| ADD    | `agents/approver/skill-set.yaml` | From v2 |
| ADD    | `agents/approver/skills-template/approval-strategy.md` | From v2 |
| ADD    | `agents/approver/skills-template/decision-approval.md` | From v2 |
| ADD    | `agents/approver/skills-template/plan-approval.md` | From v2 |

### Pair 2 — developer

| Action | Path | Notes |
|--------|------|-------|
| REMOVE | `agents/developer/growth.md` | Base-only (per task context); absent in v2 |
| REMOVE | `agents/developer/user.md` | Base-only; absent in v2 |
| REPLACE | `agents/developer/memory.md` | Present in v2; v2 memory travels AS-IS (D3 wrinkle, NOTED) |
| REPLACE | `agents/developer/meta.json` | New content per v2 (version="2.0.0"; skill_injection=false; team_members=["coder","worker"]) |
| REPLACE | `agents/developer/rule.md` | New content per v2 |
| REPLACE | `agents/developer/soul.md` | New content per v2 |
| REPLACE | `agents/developer/tools_note.md` | New content per v2 |
| REPLACE | `agents/developer/workflow.md` | New content per v2 |
| ADD    | `agents/developer/skill-set.yaml` | From v2 |
| ADD    | `agents/developer/skills-template/dev-strategy.md` | From v2 |

### Pair 3 — planner

| Action | Path | Notes |
|--------|------|-------|
| REMOVE | `agents/planner/memory.md` | Base-only; absent in v2 |
| REPLACE | `agents/planner/meta.json` | New content per v2 (version="2.0.0") |
| REPLACE | `agents/planner/rule.md` | New content per v2 |
| REPLACE | `agents/planner/soul.md` | New content per v2 |
| REPLACE | `agents/planner/tools_note.md` | New content per v2 |
| REPLACE | `agents/planner/workflow.md` | New content per v2 |
| ADD    | `agents/planner/skill-set.yaml` | From v2 |
| ADD    | `agents/planner/skills-template/plan-creation.md` | From v2 |
| ADD    | `agents/planner/skills-template/planning-strategy.md` | From v2 |
| ADD    | `agents/planner/skills-template/requirements-analysis.md` | From v2 |
| ADD    | `agents/planner/skills-template/roadmap-strategy.md` | From v2 |
| ADD    | `agents/planner/skills-template/technical-analysis.md` | From v2 |

### Pair 4 — reviewer

| Action | Path | Notes |
|--------|------|-------|
| REPLACE | `agents/reviewer/memory.md` | Present in v2; v2 memory travels AS-IS (D3 wrinkle, NOTED) |
| REPLACE | `agents/reviewer/meta.json` | New content per v2 (version="2.0.0") |
| REPLACE | `agents/reviewer/rule.md` | New content per v2 |
| REPLACE | `agents/reviewer/soul.md` | New content per v2 |
| REPLACE | `agents/reviewer/tools_note.md` | New content per v2 |
| REPLACE | `agents/reviewer/workflow.md` | New content per v2 |
| ADD    | `agents/reviewer/skill-set.yaml` | From v2 |
| ADD    | `agents/reviewer/skills-template/architecture-review.md` | From v2 |
| ADD    | `agents/reviewer/skills-template/business-logic-review.md` | From v2 |
| ADD    | `agents/reviewer/skills-template/code-review.md` | From v2 |
| ADD    | `agents/reviewer/skills-template/plan-review.md` | From v2 |
| ADD    | `agents/reviewer/skills-template/pr-review.md` | From v2 |
| ADD    | `agents/reviewer/skills-template/review-strategy.md` | From v2 |
| ADD    | `agents/reviewer/skills-template/security-review.md` | From v2 |

### Pair 5 — tidier

| Action | Path | Notes |
|--------|------|-------|
| REMOVE | `agents/tidier/memory.md` | Base-only; absent in v2 |
| REPLACE | `agents/tidier/meta.json` | New content per v2 (version="2.0.0") |
| REPLACE | `agents/tidier/rule.md` | New content per v2 |
| REPLACE | `agents/tidier/soul.md` | New content per v2 |
| REPLACE | `agents/tidier/tools_note.md` | New content per v2 |
| REPLACE | `agents/tidier/workflow.md` | New content per v2 |
| ADD    | `agents/tidier/skill-set.yaml` | From v2 |
| ADD    | `agents/tidier/skills-template/tidier-readable-code.md` | From v2 |
| ADD    | `agents/tidier/skills-template/tidier-robustness.md` | From v2 |
| ADD    | `agents/tidier/skills-template/tidier-static-hygiene.md` | From v2 |
| ADD    | `agents/tidier/skills-template/tidier-strategy.md` | From v2 |

### NOT touched (read-only)

- All `agents/*[v2]/...` paths (binding D4).
- `agents/{architect,ari,blueprinter,charter,coder,critic,designer,devops,doc-maintainer,doc-writer,experiencer,explorer,gaia,giter,governor,image-comparator,image-reader,jober,kb-importer,kb-writer,leader,maintenancer,project-manager,sketcher,skill-keeper,tester,wanderer,watcher,worker}/` — 29 other base dirs.
- `agents/_baby_template/`, `agents/_inner_soul/`, `agents/_mother/`, `agents/_prompt_system/` — 4 infra dirs.

## Test Pin Updates Required (D-B3)

The following test files pin to pre-copy base values and MUST be updated inside Phase B's PR (not deferred). The pre-copy grep pass is exhaustive — the executor MUST re-grep at execution time to catch any new pin sites added between plan and execution.

### R9 — Real base-agent pin set (architect-verified 2026-10-13)

> **R9 architect verdict: the "~12 meta pin test files" list in earlier drafts is LARGELY MISATTRIBUTED.** The real base-agent pin set is concentrated in `tests/test_innate_skills_refactoring.py`; many lines previously listed either (a) are NOT developer/agent pins, (b) are HealthResponse fixtures, (c) are synthetic test-agent fixtures, or (d) do not exist. The corrected set follows.

### Verified pin sites (R9-corrected — re-grep at execution time)

| Test file | Pin pattern (pre-copy) | Required post-copy value | Why |
|-----------|------------------------|--------------------------|-----|
| **`tests/test_innate_skills_refactoring.py`** | `("developer", ["opencode", "chart", "todo"], "OpenCode-Skill")` + 4 sibling tuples at `:59-65` (reviewer/planner/tidier/approver) | Replace each tuple's `innate_skills` list with `["todo", "chart", "dynamic-skill"]`; replace the expected skill name `OpenCode-Skill` with a v2-era skill (e.g., `Dynamic Skill`) | **REAL base-agent pin site** — 5 tuples pin the 5 retiring base agents to their opencode-era meta.json shape. Post-B these meta.json values no longer exist; the fixture must match v2. |
| **`tests/test_innate_skills_refactoring.py`** | Loose "opencode" loader examples at `:227` (test with `innate_skills: ["opencode", "nonexistent_skill"]`) and `:271` (test with `innate_skills: ["opencode"]`) | Rephrase the test agents' `innate_skills` to a surviving skill (e.g., `["chart", "nonexistent_skill"]` for `:227`; `["chart"]` for `:271`); keep the loader semantics testable | The fixture purpose is loader-resolves-innate-skill-from-dir; the chosen skill must still exist in `agents/_prompt_system/innate-skills/` post-C. |
| **`tests/test_innate_skills_refactoring.py`** | `agents_with_opencode = real_registry.find_skill("opencode")` at `:299` (asserts return value is `["approver","developer","planner","reviewer","tidier"]`) | Rephrase to query a surviving innate skill (e.g., `find_skill("chart")` or another post-C survivor) and assert the return value matches v2's `innate_skills` declarations | Post-B + post-C the `opencode` skill dir is archived; `find_skill("opencode")` returns `[]`. The test's INTENT (registry surfaces agents declaring a given innate skill) is preserved by rephrasing the skill name. |

### ⛔ EXCLUSION WARNING (R9, CRITICAL — do NOT touch)

| File | Line(s) | What it is | Why NOT to edit |
|------|---------|------------|------------------|
| `tests/conftest.py` | `:443` (inside the `sample_health_response_data` fixture at `:437-444`) | The HealthResponse/daemon-version fixture. The `"version": "1.0.0"` here represents the **daemon's reported version** (the fixture is consumed by `HealthResponse` consumers, e.g., `tests/test_models.py:276,:289`), NOT an agent meta.json pin. Daemon reports `0.20.1` per `daemon/__init__.py`. | Editing this to `"2.0.0"` ACTIVELY BREAKS the test's semantics — the HealthResponse fixture should match the daemon's actual version, not the developer agent's v2 version. **The executor MUST NOT touch this line.** |

### De-listed non-pins (R9 — do NOT add back to the pin list)

The following sites were previously listed as pin sites but are VERIFIED NON-PINS (architect + 3-worker converged 2026-10-13):

| File | Line(s) | What it actually is | Why NOT a pin |
|------|---------|---------------------|----------------|
| `tests/test_agents_api.py` | `:40` | A `temp_agents_dir` synthetic test-agent `meta.json` write (`{"id": "developer", ..., "version": "1.0.0"}`) used to set up a fixture for the API test. The "developer" id here is the test's AGENT DIR name, not the production developer agent. | Synthetic fixture; the `version: "1.0.0"` represents the API's `create_agent` default backfill behavior (`daemon/routers/agents.py:119,:135`) — not a developer-agent pin. |
| `tests/test_agents_api.py` | `:173` | Assertion that the create-agent API backfills `"version": "1.0.0"` for a freshly-created `test-agent` | Same as `:40` — tests the API's default backfill, not developer |
| `tests/test_agents_api.py` | `:443` | **DOES NOT EXIST** (file is 383 lines total) | Stale citation; the executor MUST NOT chase this line |
| `tests/test_registry.py` | `:871` | A synthetic leader-agent `meta.json` fixture (`{"id": "leader", ...}`) used in a leader-specific test. The `version: "1.0.0"` is the test's own fixture, not a developer pin. | Leader is NOT in the migration set; this fixture stays as-is. |
| `tests/test_models.py` | `:276, :289` | HealthResponse model tests (consume the `sample_health_response_data` fixture from conftest) | HealthResponse tests, not developer pins |
| `tests/test_agents_api.py` | (no `:443`) | — | — |

### Sites re-grep at execution time (defensive sweep)

The executor MUST re-grep at execution time to catch any drift from the architect-verified set above:

```bash
# R9 authoritative grep: confirm the 3 real pin sites + check for new ones
grep -rn --include="*.py" -E '\("developer", \["opencode"' tests/         # expect hit in test_innate_skills_refactoring.py:59
grep -rn --include="*.py" 'find_skill("opencode")' tests/                  # expect hit in test_innate_skills_refactoring.py:299
grep -rn --include="*.py" -E 'innate_skills.*\["opencode"' tests/          # expect 5+ hits in test_innate_skills_refactoring.py

# R9 negative checks: ensure the de-listed sites are NOT in the pin list
grep -n '"version": "1.0.0"' tests/test_agents_api.py tests/conftest.py tests/test_models.py tests/test_registry.py
# Expect: de-listed lines may exist but ARE NOT developer/agent pins (HealthResponse, synthetic fixtures, leader test)
# The executor MUST NOT touch them based on this list alone — each must be re-evaluated against its surrounding context
```

### Other test files from the prior (over-broad) list — disposition

The following files were in the prior list but are NOT in the corrected R9 pin set; the executor treats them as **out of scope** for Phase B's test-pin-update unless a NEW pin is found at execution time:

| File | Disposition |
|------|-------------|
| `tests/test_models.py` | NOT a pin site (HealthResponse consumer only) |
| `tests/test_spawn_instance_instructive_errors.py` | NOT a pin site (per R9 de-list) |
| `tests/integration/test_skill_bank_router.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/test_devops_agent.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/test_skill_bank_repository.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/test_models_split.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/test_reviewer_v2_agent.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/test_mcp_tool_filter.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/test_skill_seeding.py` | NOT a pin site (per R9 de-list; the v2==base idempotency property still holds) |
| `tests/unit/tools/test_version_tag_tool_resolution.py` | NOT a pin site (per R9 de-list) |
| `tests/unit/tools/test_attestation_registration.py` | NOT a pin site (per R9 de-list) |
| `tests/test_tool_filter.py` | NOT a Phase-B pin site — it IS a Phase-C TOUCH site (R1 in architecture-recommendation: 3 tests at `:710-716,:750-764,:827-860` go red at C.1 and need rephrase). The Phase-B pin update does NOT touch it; the Phase-C wiring excision does. |

**Step 11 (executor)** must run:
```bash
# Re-grep at execution time; any new pin site = new row in this table
grep -rn --include="*.py" -E '"version": "1\.0\.0"|innate_skills.*opencode' tests/ \
    | tee .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.txt
# This is the FINAL pin-site list; test-pin-sites.txt becomes the executable's
# checklist for the test pin update step.
```

## Dependencies & Ordering

- **Upstream**: Phase A must be **DONE** (DoD-A1 through DoD-A10 all green). Phase A's `legacy/` is the safety net; if Phase B's per-pair copy fails partway, rollback is `rm base/<diff-files>` + `cp -R legacy/agents/<name>/. base/<name>/` to restore.
- **Downstream**: Phase C (opencode wiring excision + test move/strip) and Phase D (doc references) run after Phase B. Phase E reads Phase B's evidence files for whole-mission pack.
- **Sibling ordering**: Phase B and Phase A's verification batch are sequential — Phase B cannot start until Phase A emits `evidence/phaseA/diffs/*.diff.txt` showing the archive is sound.
- **ENGINEERING-CRITICAL ORDERING CONSTRAINT (cross-reference D-A8 in `phaseA-legacy-archive.md`):** while the 5 old-base agents still declare `"innate_skills": ["opencode", "chart", "todo"]` in their CURRENT base `meta.json` (worktree HEAD `885a19d4f`), the innate-skills loader MUST resolve the `opencode` innate skill — both its prompt dir (`agents/_prompt_system/innate-skills/opencode/`, archived by Phase A) and its grant-map entry (`daemon/tools/instance.py:162 INNATE_SKILL_TOOL_CATEGORIES`). Phase B's base := v2 copy retires these 5 `meta.json` declarations (replacing them with v2's `["todo", "chart", "dynamic-skill"]`), making the opencode innate-skill loader path unreachable. After Phase B lands, the opencode stack removal (Phase C) becomes safe — but Phase C MUST NOT execute before Phase B's copy is in place. Phase A's pure-copy archive is safe anytime and does not bind Phase B.
- **Practical sequencing implication**: if Phase A, Phase B, and Phase C execute as separate PRs, the mission order MUST be A → B → C with each landed/promoted before the next starts. If they execute in the same PR (mission co-located model), the constraint is satisfied because Phase B copies the meta.json BEFORE Phase C removes the stack. The mission PR author documents this sequencing in the PR description.

## Step-by-Step Operations

### Step 1 — Worktree + FAKE_HOME fence (re-run; Phase A's fence is per-script invocation, not persisting)

```bash
cd /home/nea/ensemble-src-wt-agent-base-v2
git rev-parse --abbrev-ref HEAD         # MUST print: feature/agent-base-v2
git rev-parse HEAD                       # MUST print: 885a19d4f45a3403a1bc615ec0f393630c938c84
git status --porcelain                   # MUST be empty (or only legacy/ + evidence/ changes from Phase A)
# FAKE_HOME fence (project critical note 2026-10-12)
export FAKE_HOME="$(mktemp -d -t agent-base-v2-XXXXXX)"
trap 'cd - >/dev/null 2>&1; rm -rf "$FAKE_HOME"' EXIT
# Sanity: any script that touches $HOME-abspath MUST use $FAKE_HOME or worktree-anchored paths.
```

### Step 2 — Re-verify per-pair file inventory at execution start

> **S8 clarifying line:** the empty `evidence/phaseB/` scaffold directory is **INTENTIONALLY UNPOPULATED at planning time** — it is populated during execution by the Steps below. The presence of the empty `evidence/phaseB/` directory in the planning dir at planning time is EXPECTED (the executor's `mkdir -p` calls in Steps 2–12 create the per-section subdirs). The same applies to phaseA's `evidence/phaseA/` and the inventory-check/v2-before-manifests/base-before-manifests/per-agent-ops/diffs subdirs — they are execution-time artifacts, not pre-existing plan content.

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check

# Pair inventories vs recorded plan table.
exec_pairs="approver developer planner reviewer tidier"
for name in $exec_pairs; do
    find "agents/$name" -type f | sort > ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/base-$name.filelist.txt"
    find "agents/$name[v2]" -type f | sort > ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/v2-$name.filelist.txt"
done

# Verify the inventory matches the plan table:
# approver base: 6 files (memory, meta, rule, soul, tools_note, workflow)
# approver v2:   9 files (meta, rule, soul, tools_note, workflow, skill-set.yaml, skills-template/{approval-strategy, decision-approval, plan-approval}.md)
# developer base: 8 files (growth, memory, meta, rule, soul, tools_note, user, workflow)
# developer v2:   8 files (memory, meta, rule, soul, tools_note, workflow, skill-set.yaml, skills-template/dev-strategy.md)
# planner base:  6 files
# planner v2:    11 files (+ skill-set.yaml + 5 skills-template/*.md)
# reviewer base: 6 files
# reviewer v2:   14 files (memory, meta, rule, soul, tools_note, workflow, skill-set.yaml, 7 skills-template/*.md)
# tidier base:   6 files
# tidier v2:     10 files (meta, rule, soul, tools_note, workflow, skill-set.yaml, 4 skills-template/*.md)
for name in $exec_pairs; do
    base_n=$(wc -l < ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/base-$name.filelist.txt" | tr -d ' ')
    v2_n=$(wc -l < ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/v2-$name.filelist.txt" | tr -d ' ')
    echo "$name  base=$base_n  v2=$v2_n"
done
```

Per-step verification:
- **Expected-at-planning (DISK TRUTH, 2026-10-13, triple-confirmed)**: `approver base=6 v2=9`, `developer base=8 v2=8`, `planner base=6 v2=11`, `reviewer base=6 v2=14`, `tidier base=6 v2=10`. Grand total across the 5 v2 trees: **52 files** (9 + 8 + 11 + 14 + 10). Any deviation is a Phase B hard stop (re-grep against the worktree to find drift).
- **DYNAMIC DERIVATION (RECOMMENDED, prevents drift)**: at runtime, the executor should NOT rely on the hard-coded numbers above. Instead, compute the counts from the actual worktree and assert equality:

  ```bash
  # Dynamic count gate: each pair's base count must equal v2 count; grand total must equal 52.
  GRAND=0
  for name in approver developer planner reviewer tidier; do
      bc=$(find "agents/$name"      -type f | wc -l | tr -d ' ')
      vc=$(find "agents/$name[v2]" -type f | wc -l | tr -d ' ')
      if [ "$bc" != "$vc" ]; then
          echo "HALT: $name base($bc) != v2($vc) — surgical copy will diverge from v2 byte-identity. Re-grep and re-investigate." >&2
          exit 1
      fi
      GRAND=$((GRAND + vc))
      echo "  $name  base=$bc  v2=$vc  (match)"
  done
  echo "Grand total v2 files: $GRAND (must equal 52)"
  test "$GRAND" -eq 52 || { echo "HALT: grand total $GRAND != 52"; exit 1; }
  ```

  This dynamic gate prevents the off-by-one class of bug that the prior hard-coded values carried (4/5 pairs were off-by-one in an earlier draft; the dynamic form cannot drift). Keep the hard-coded "expected-at-planning" annotation above as audit context for the 2026-10-13 snapshot — but the runtime gate is the dynamic form.

### Step 3 — Capture v2 BEFORE-snapshot manifests (D4 ZERO-TOUCH proof, pre-work)

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests

_make_manifest() {
    local src="$1"; local out="$2"
    ( cd "$src" && find . -type f -not -path './__pycache__/*' \
        | sed 's|^\./||' | sort \
        | while IFS= read -r f; do
            [ -n "$f" ] || continue
            printf '%s  %s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$f"
        done
    ) > "$out"
    shasum -a 256 "$out" | awk '{print $1}' > "$out.sha256"
}

exec_pairs="approver developer planner reviewer tidier"
for name in $exec_pairs; do
    _make_manifest "agents/$name[v2]" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests/v2-$name.manifest.txt"
done
# Append a manifest-of-manifests so the entire v2-before state has one verifiable digest.
cat .agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests/*.manifest.txt \
    | shasum -a 256 \
    > .agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests/v2-before-aggregate.sha256
```

Per-step verification:
- 5 manifest files + 5 .sha256 files + 1 aggregate .sha256 all non-empty.

### Step 4 — Capture base BEFORE-snapshot manifests (rollback fallback audit)

> Capturing base before-state is a per-pair audit only (the actual backup is in `legacy/agents/<name>/` per Phase A). This snapshot provides byte-identical evidence of what base looked like before the surgical ops.

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/base-before-manifests

exec_pairs="approver developer planner reviewer tidier"
for name in $exec_pairs; do
    _make_manifest "agents/$name" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/base-before-manifests/base-$name.before.manifest.txt"
done
```

### Step 5 — Capture CURRENT base-file inventory for surgical delete

```bash
# For each pair, identify the files to REMOVE (base-only) vs KEEP (also in v2, will be replaced).
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/per-agent-ops

for name in $exec_pairs; do
    base_list=".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/base-$name.filelist.txt"
    v2_list=".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/v2-$name.filelist.txt"
    # Strip the leading "agents/<name>/" from base_list and "agents/<name>[v2]/" from v2_list
    sed "s|^agents/$name/||" "$base_list" > "$base_list.relative"
    sed "s|^agents/$name\[v2\]/||" "$v2_list" > "$v2_list.relative"
    # Per-pair ops file:
    ops=".agents/shared/planning/agent-base-v2/evidence/phaseB/per-agent-ops/$name.ops.md"
    {
        echo "# Per-agent ops for $name"
        echo "## REMOVE (base-only files not in v2)"
        comm -23 <(sort "$base_list.relative") <(sort "$v2_list.relative") | sed 's|^|- `agents/'$name'/|'
        echo "## ADD (v2-only files not yet in base)"
        comm -13 <(sort "$base_list.relative") <(sort "$v2_list.relative") | sed 's|^|- `agents/'$name'/|'
        echo "## REPLACE (files present in both)"
        comm -12 <(sort "$base_list.relative") <(sort "$v2_list.relative") | sed 's|^|- `agents/'$name'/|'
    } > "$ops"
done
```

Per-step verification (sanity):
- For each pair, the REMOVE list should match the "REMOVE" rows in the plan inventory table; the ADD list should match "ADD"; the REPLACE list should match "REPLACE".

### Step 6 — Surgical copy per pair (5 independent stages)

```bash
# Sub-helper for each pair:
_copy_pair() {
    local name="$1"
    local base_dir="agents/$name"
    local v2_dir="agents/$name[v2]"
    local base_list=".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/base-$name.filelist.txt.relative"
    local v2_list=".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/v2-$name.filelist.txt.relative"

    # 6a. Per-v2-file: copy into base, preserving permissions + timestamps.
    ( cd "$v2_dir" && find . -type f \
        -not -path './__pycache__/*' \
        -exec cp -p --parents {} "../../$base_dir/" \; )

    # 6b. Per-base-only-file (comm -23): delete from base.
    comm -23 <(sort "$base_list") <(sort "$v2_list") | while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        # W2 fix: $rel is RELATIVE TO base_dir (e.g., "growth.md" or "skills-template/x.md"),
        # so the worktree-root-anchored deletion path is "agents/$name/$rel" — NOT just "$rel"
        # (which would attempt deletion at the worktree root and silently no-op for non-root files).
        rm -f "agents/$name/$rel"
        # Also remove empty parent dirs (e.g., agents/developer/skills-template/) if present
        # (the v2 copy created skills-template/; no empty dirs should remain after that step.)
    done

    # 6c. rmdir empty dirs in base that aren't in v2 (e.g., after removing growth.md/user.md,
    # neither dir-level removal is needed; skills-template/ stays because v2 has it).
    # Use find -depth to handle nested empty dirs robustly:
    find "$base_dir" -depth -type d -empty -delete || true
}

# Execute per-pair, in order:
_copy_pair approver
_copy_pair developer
_copy_pair planner
_copy_pair reviewer
_copy_pair tidier
```

Per-step verification (after each `_copy_pair`):

```bash
# Inventory re-check
for name in $exec_pairs; do
    diff -r "agents/$name" "agents/$name[v2]" || echo "MISMATCH: $name vs $name[v2]"
done
# Expected: NO output across all 5 pairs.

# Per-file sha256 (D-B2 verification — byte-identical regardless of cp metadata):
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/base-after-manifests
for name in $exec_pairs; do
    _make_manifest "agents/$name" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/base-after-manifests/base-$name.after.manifest.txt"
done

# Diff base-after vs v2-before per pair:
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/diffs
for name in $exec_pairs; do
    diff_file=".agents/shared/planning/agent-base-v2/evidence/phaseB/diffs/$name.base-vs-v2.diff.txt"
    diff \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/base-after-manifests/base-$name.after.manifest.txt" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests/v2-$name.manifest.txt" \
        > "$diff_file" || true
    # Note: relative paths differ — base lists "agents/<name>/<rel>", v2 lists "agents/<name>[v2]/<rel>".
    # The actual content diff must be: every line is "<sha>  ..." with the SAME sha.
    # Path-prefix-normalize diff for reviewer safety:
    sed "s|^agents/$name/|agents/$name\[v2\]/|" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/base-after-manifests/base-$name.after.manifest.txt" \
        > "$diff_file.base-normalized"
    diff "$diff_file.base-normalized" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests/v2-$name.manifest.txt" \
        > "$diff_file" || true
done
```

Per-pair verification gate: for each name in `approver developer planner reviewer tidier`:
- `diffs/<name>.base-vs-v2.diff.txt` MUST be empty.
- Base-after manifest line count == v2-before manifest line count.

### Step 7 — v2 AFTER-snapshot manifest (D4 ZERO-TOUCH final proof)

```bash
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/v2-after-manifests

exec_pairs="approver developer planner reviewer tidier"
for name in $exec_pairs; do
    _make_manifest "agents/$name[v2]" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/v2-after-manifests/v2-$name.after.manifest.txt"
done

# Compare v2-before vs v2-after per pair — must be empty.
mkdir -p .agents/shared/planning/agent-base-v2/evidence/phaseB/diffs
for name in $exec_pairs; do
    diff_file=".agents/shared/planning/agent-base-v2/evidence/phaseB/diffs/$name.v2-before-vs-v2-after.diff.txt"
    diff \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/v2-after-manifests/v2-$name.after.manifest.txt" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/v2-before-manifests/v2-$name.manifest.txt" \
        > "$diff_file" || true
done
```

Per-step verification:
- All 5 v2 before/after diffs must be empty (D4 proof).

### Step 8 — Git-level proof of scope (other agents + infra dirs + v2 untouched)

```bash
# 8a. v2 ZERO-TOUCH — must be empty.
v2_dirty=$(git status --porcelain -- ':(literal)agents/developer[v2]' ':(literal)agents/reviewer[v2]' ':(literal)agents/tidier[v2]' ':(literal)agents/approver[v2]' ':(literal)agents/planner[v2]')
[ -z "$v2_dirty" ] && echo "PASS: v2 untouched" > .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt
[ -n "$v2_dirty" ] && echo "FAIL: v2 modified:" >> .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt
[ -n "$v2_dirty" ] && echo "$v2_dirty" >> .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt

# 8b. The 5 base agent dirs are the ONLY expected dirty ones; record all dirty paths.
git status --porcelain -- 'agents/' \
    | tee -a .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt

# 8c. 4 infra dirs untouched.
infra_dirty=$(git status --porcelain -- 'agents/_baby_template' 'agents/_inner_soul' 'agents/_mother' 'agents/_prompt_system')
[ -z "$infra_dirty" ] && echo "PASS: infra dirs untouched" >> .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt
[ -n "$infra_dirty" ] && echo "FAIL: infra dirs dirty:" >> .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt
[ -n "$infra_dirty" ] && echo "$infra_dirty" >> .agents/shared/planning/agent-base-v2/evidence/phaseB/scope-checks.txt
```

Per-step verification:
- `scope-checks.txt` lines from 8a must start with `PASS: v2 untouched` (no FAIL for v2).
- 8c must start with `PASS: infra dirs untouched`.
- 8b must contain ONLY entries under `agents/{approver,developer,planner,reviewer,tidier}/`. Any entry under `agents/<other>/` is a Phase B FAILS — investigate before continuing.

### Step 9 — Per-pair inventory re-verification (final state matches expected file sets)

```bash
for name in $exec_pairs; do
    find "agents/$name" -type f | sed "s|^agents/$name/||" | sort \
        > ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/base-$name.filelist.txt.relative.post"
    # Each pair's final relative file list must EXACTLY match v2's relative file list.
    diff \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/base-$name.filelist.txt.relative.post" \
        ".agents/shared/planning/agent-base-v2/evidence/phaseB/inventory-check/v2-$name.filelist.txt.relative" \
        > ".agents/shared/planning/agent-base-v2/evidence/phaseB/diffs/$name.filelist.diff.txt" || true
done
```

Per-step verification:
- All 5 filelist diffs must be empty.

### Step 10 — Release-tools-shaped digest (matches stage.sh family)

```bash
# Produce the aggregate manifest-line set the same way stage.sh does (lib.sh:1928 _tree_manifest),
# restricted to the 5 affected base dirs (so the digest is comparable pre/post promote).
exec_pairs="approver developer planner reviewer tidier"
{
    for name in $exec_pairs; do
        # base dir (now = v2 content); print "agents/<name>/<rel>" sha + path lines
        ( cd "agents/$name" && find . -type f \
            | sed 's|^\./||' | sort \
            | while IFS= read -r f; do
                [ -n "$f" ] || continue
                printf '%s  agents/%s/%s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$name" "$f"
            done
        )
        # v2 dir (untouched); print "agents/<name>[v2]/<rel>" sha + path lines
        ( cd "agents/$name[v2]" && find . -type f \
            | sed 's|^\./||' | sort \
            | while IFS= read -r f; do
                [ -n "$f" ] || continue
                printf '%s  agents/%s[v2]/%s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$name" "$f"
            done
        )
    done
} | sort -k2 \
    > .agents/shared/planning/agent-base-v2/evidence/phaseB/agents-tree.5pairs.txt

# Aggregate tree digest for the 5 affected pairs.
shasum -a 256 .agents/shared/planning/agent-base-v2/evidence/phaseB/agents-tree.5pairs.txt \
    | awk '{print $1}' \
    > .agents/shared/planning/agent-base-v2/evidence/phaseB/agents-tree.5pairs.sha256

# Also verify stage.sh would emit the SAME agents-tree line if it ran now.
# This is the cross-check Phase E uses.
{
    # 29 non-affected base dirs + 4 infra dirs (use single walks)
    for d in agents/architect agents/ari agents/blueprinter agents/charter agents/coder agents/critic agents/designer agents/devops agents/doc-maintainer agents/doc-writer agents/experiencer agents/explorer agents/gaia agents/giter agents/governor agents/image-comparator agents/image-reader agents/jober agents/kb-importer agents/kb-writer agents/leader agents/maintenancer agents/project-manager agents/sketcher agents/skill-keeper agents/tester agents/wanderer agents/watcher agents/worker agents/_baby_template agents/_inner_soul agents/_mother agents/_prompt_system; do
        ( cd "$d" 2>/dev/null && find . -type f \
            | sed 's|^\./||' | sort \
            | while IFS= read -r f; do
                [ -n "$f" ] || continue
                printf '%s  %s/%s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$d" "$f"
            done
        )
    done
} | sort -k2 \
    > .agents/shared/planning/agent-base-v2/evidence/phaseB/agents-tree.other-bases.txt
```

Per-step verification:
- `agents-tree.5pairs.sha256` is 64 hex chars; line count of `agents-tree.5pairs.txt` == **52** (sum of v2 file counts per DISK TRUTH 2026-10-13: approver=9 + developer=8 + planner=11 + reviewer=14 + tidier=10 = 52 files, ×1 each since base now == v2, no double counting — each appears once in the manifest with relpath-prefixed). Resolve the prior self-contradiction (`49/=50 files`): the line count MUST equal 52, the grand total MUST equal 52; the dynamic gate in Step 2's verification prevents drift.
- `agents-tree.other-bases.txt` is non-empty; this is the diff input Phase E uses to compute the FULL post-copy `agents_tree_sha256` surrogate (sum with 5-pairs digest).

### Step 11 — Test pin update (R9-CORRECTED, D-B3)

```bash
# R9 (architect-verified 2026-10-13): the real base-agent pin set is concentrated
# in tests/test_innate_skills_refactoring.py. Many sites previously listed were
# misattributed (HealthResponse fixtures, synthetic test-agent fixtures, leader
# fixtures, nonexistent lines). The corrected set is 3 sub-tables; see "Test Pin
# Updates Required" above for full citations + de-list rationale.

# Re-grep at execution time, capture final pin-site list (R9 authoritative grep):
grep -rn --include="*.py" -E '\("developer", \["opencode"' tests/ \
    | tee .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.txt
grep -rn --include="*.py" 'find_skill("opencode")' tests/ \
    | tee -a .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.txt

# Apply surgical edits per R9's verified pin set (3 sites, all in test_innate_skills_refactoring.py):
#
# Required edits (R9-corrected; re-verify line numbers at execution time):
#   1. tests/test_innate_skills_refactoring.py:59-65 — the 5-tuple test fixture.
#      Replace each `("developer", ["opencode", "chart", "todo"], "OpenCode-Skill")`-style
#      tuple's `innate_skills` list with `["todo", "chart", "dynamic-skill"]` and the
#      expected skill name with a v2-era skill (e.g., "Dynamic Skill"). Apply to all 5
#      tuples (developer / reviewer / planner / tidier / approver).
#   2. tests/test_innate_skills_refactoring.py:227 — `innate_skills: ["opencode", "nonexistent_skill"]`
#      test agent. Rephrase to a surviving skill (e.g., `["chart", "nonexistent_skill"]`)
#      and create the matching innate-skills dir if needed.
#   3. tests/test_innate_skills_refactoring.py:271 — `meta = {"id": "conflict_agent", "innate_skills": ["opencode"]}`
#      Rephrase to a surviving skill (e.g., `["chart"]`) and ensure the test's innate-skills
#      dir is consistent.
#   4. tests/test_innate_skills_refactoring.py:299 — `agents_with_opencode = real_registry.find_skill("opencode")`
#      Rephrase the queried skill name (e.g., to `"chart"` or another post-C survivor)
#      AND update the expected `["approver","developer","planner","reviewer","tidier"]`
#      list to match which agents declare the chosen skill in v2 meta.json.
#
# ⛔ DO NOT TOUCH (R9 exclusion warning):
#   - tests/conftest.py:443 — the `sample_health_response_data` HealthResponse/daemon-version
#     fixture (`"version": "1.0.0"` represents the daemon's reported version, NOT a developer
#     pin). Editing this to "2.0.0" ACTIVELY BREAKS the test's semantics. The executor MUST
#     verify this line is unchanged after Step 11.
#
# Use edit_file per site: the per-site changes must be IDENTIFIED BY GREP first.
# After each edit, immediately re-grep the file to confirm the old value is gone.
# After Step 11, run a negative check to confirm the conftest HealthResponse line is intact.
#
# Capture post-edit re-grep:
grep -rn --include="*.py" -E '\("developer", \["opencode"|find_skill\("opencode"\)' tests/ \
    | tee .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.post-edit.txt
# PASS criterion: empty (no remaining opencode-pinned base-agent tuples or find_skill("opencode") in tests/).

# R9 negative check: confirm conftest HealthResponse line is UNCHANGED
grep -n '"version": "1.0.0"' tests/conftest.py | head -1 \
    | tee -a .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.post-edit.txt
# Expected: line 443 is reported as still containing "version": "1.0.0" (the HealthResponse fixture, unchanged).
```

Per-step verification:
- `test-pin-sites.txt` (pre-edit) is non-empty (contains the 3 R9 pin sites + 1 R9 negative-check baseline).
- `test-pin-sites.post-edit.txt` is empty for the 3 R9 pin sites + 1 line confirming `tests/conftest.py:443` is unchanged.
- The negative-check entry for `conftest.py:443` MUST appear in the post-edit output; absence indicates the executor mistakenly edited the HealthResponse fixture.

### Step 12 — Skill seeding idempotency audit (per Risk #4)

> `daemon/skills/skill_seed_service.py:285-292` resolves `developer[v2]` → base id `developer`; `:436` makes same-version idempotent. Post-copy, base `developer` content has v2 skill-set.yaml (version 1.3.0 for dev-strategy). Daemon restart re-walks and seeds; if bank already holds `developer/dev-strategy@1.3.0` (seeded from v2 historically), the same-version check skips — no double-seed side effects.

```bash
# Capture the pre-promote state of the skill bank for the 5 base agents.
# This is a READ-ONLY operation against the live bank (no daemon restart).
# If the live daemon is unreachable, record the result as N/A and proceed (the
# idempotency guarantee is encoded in skill_seed_service.py:436 — phase B's
# audit's purpose is documentation, not enforcement).
python3 -c "
import os, json, pathlib
# No DB connection needed; the audit is conceptual. Skip if the bank isn't
# reachable from this worktree.
print('SKILL_BANK_AUDIT: skipped at planning time; documented for executor')
" > .agents/shared/planning/agent-base-v2/evidence/phaseB/skill-bank-audit.txt 2>&1

# The executor, when running with access to the live bank, should query:
#   SELECT agent_id, name, template_version FROM skill_bank_templates
#   WHERE agent_id IN ('approver','developer','planner','reviewer','tidier');
# and confirm each row's template_version equals the v2 skill-set.yaml version:
#   approver/{approval-strategy:1.1.0, plan-approval:1.2.0, decision-approval:1.2.0}
#   developer/{dev-strategy:1.3.0}
#   planner/{planning-strategy:1.1.0, plan-creation:1.2.0, roadmap-strategy:1.2.0, requirements-analysis:1.2.0, technical-analysis:1.2.0}
#   reviewer/{review-strategy:1.1.0, code-review:1.2.0, plan-review:1.2.0, architecture-review:1.2.0, business-logic-review:1.2.0, pr-review:1.2.0, security-review:1.2.0}
#   tidier/{tidier-strategy:1.1.0, tidier-readable-code:1.2.0, tidier-robustness:1.2.0, tidier-static-hygiene:1.2.0}
# Any deviation = NEW seed expected. Same version = idempotent skip (no double-seed).
```

Per-step verification:
- `skill-bank-audit.txt` exists with non-empty content (even if the audit is deferred to the live-bank state inspection at user promote time).

### Step 13 — Per-step summary emission

```bash
{
    echo "Phase B Summary"
    echo "==============="
    echo "Branch: $(git rev-parse --abbrev-ref HEAD)"
    echo "HEAD:   $(git rev-parse HEAD)"
    echo "Pairs executed: approver developer planner reviewer tidier"
    echo
    echo "v2 untouched (D4 proof):"
    for name in $exec_pairs; do
        sz=$(wc -c < ".agents/shared/planning/agent-base-v2/evidence/phaseB/diffs/$name.v2-before-vs-v2-after.diff.txt" | tr -d ' ')
        echo "  $name v2-before==v2-after diff size: $sz bytes (must be 0)"
    done
    echo
    echo "base==v2 byte-identical (post-copy proof):"
    for name in $exec_pairs; do
        sz=$(wc -c < ".agents/shared/planning/agent-base-v2/evidence/phaseB/diffs/$name.base-vs-v2.diff.txt" | tr -d ' ')
        echo "  $name base-after==v2-before diff size: $sz bytes (must be 0)"
    done
    echo
    echo "Test pin update pre-edit grep size: $(wc -l < .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.txt) (must be > 0)"
    echo "Test pin update post-edit grep size: $(wc -l < .agents/shared/planning/agent-base-v2/evidence/phaseB/test-pin-sites.post-edit.txt) (must be 0)"
} > .agents/shared/planning/agent-base-v2/evidence/phaseB/SUMMARY.txt
cat .agents/shared/planning/agent-base-v2/evidence/phaseB/SUMMARY.txt
```

Per-step verification:
- `SUMMARY.txt` shows all 10 diff sizes = 0 bytes.

## Per-Step Verification Summary

| Step | What to check | Pass threshold |
|------|---------------|----------------|
| 1 | Branch + HEAD match; FAKE_HOME created | exact text + empty porcelain (modulo Phase A artifacts) |
| 2 | Per-pair inventory matches plan table | exact line counts (6/9, 8/9, 6/10, 6/13, 6/9) |
| 3 | 5 v2-before manifests + aggregate digest exist | non-empty |
| 4 | 5 base-before manifests exist | non-empty |
| 5 | 5 per-agent-ops.md files exist with correct REMOVE/ADD/REPLACE lists | match plan inventory |
| 6 | diff -r base v2 zero output; per-pair base-vs-v2.diff empty | zero |
| 7 | 5 v2-after manifests exist; per-pair v2-before-vs-v2-after.diff empty | empty diffs (D4 proof) |
| 8 | scope-checks.txt: PASS v2 untouched, PASS infra untouched, only 5 expected dirs dirty | PASS/PASS + path-restricted dirty list |
| 9 | per-pair filelist.diff.txt empty | empty |
| 10 | agents-tree.5pairs.sha256 = 64 hex chars; line count == 52 | exact |
| 11 | test-pin-sites.post-edit.txt empty | empty |
| 12 | skill-bank-audit.txt exists | non-empty |
| 13 | SUMMARY.txt shows all zeros | all 10 lines "0 bytes" |

## Phase Rollback (git-level + per-pair)

### Per-pair rollback (single pair failed partway)

```bash
# Use legacy/agents/<name>/ as the source of truth for what base looked like before.
# Phase A captured this; if Phase A is done, rollback is safe.
name="$1"
# S3 clarification: this `git rm -rf` is the ROLLBACK-ONLY EXCEPTION to the
# no-base-dir-deletion rule (Risk #2). The dir is RECREATED in the next line
# (`cp -R legacy/agents/$name/. agents/$name/`) — net effect is content-replace,
# not dir-deletion in steady state. DO NOT interpret this as a license to `rm -rf`
# during normal execution (D-B1 + Step 6 surgical op mandate `rm -f` per file).
git rm -rf "agents/$name"           # remove the broken surgical state (rollback-only exception; recreated next line)
cp -R "legacy/agents/$name/." "agents/$name/"   # recreate dir from legacy/ snapshot
# Re-grep-test pin update rollback (more involved; see below)
```

### Full Phase B rollback (5 pairs in unexpected state)

```bash
# 1. Restore base dir contents from legacy/agents/<name>/
for name in approver developer planner reviewer tidier; do
    rm -rf "agents/$name"
    cp -R "legacy/agents/$name" "agents/$name"
done
# 2. Revert test pin edits: git checkout -- tests/  (uses git's stored pre-Phase-B state)
git checkout -- tests/
# 3. Verify v2 zero-touch holds (it should — Phase B doesn't touch v2):
#    W3 fix: 'agents/[a-z]*[v2]' is a CHARACTER CLASS (matches a single char from [a-z] + literal '*[v2]'),
#    not a glob — it is VACUOUS. Use explicit literal pathspecs (pathspec-magic :(literal) escapes
#    the brackets so the brackets are taken literally as part of the path).
git status --porcelain -- ':(literal)agents/developer[v2]' ':(literal)agents/reviewer[v2]' ':(literal)agents/tidier[v2]' ':(literal)agents/approver[v2]' ':(literal)agents/planner[v2]'
# 4. Verify base == pre-Phase-B byte for byte:
for name in approver developer planner reviewer tidier; do
    diff -r "agents/$name" "legacy/agents/$name" || echo "MISMATCH for $name (should be identical to legacy)"
done
# 5. Clean Phase B evidence artifacts:
rm -rf .agents/shared/planning/agent-base-v2/evidence/phaseB
# 6. Verify final state matches pre-Phase-A pre-Phase-B working tree (modulo legacy/ + evidence/phaseA/):
git status --porcelain | grep -v -E '^(legacy/|\?\? evidence/|\?\? .agents/shared/planning/agent-base-v2/evidence/phaseA)' || echo "clean"
```

### P6 mirror — reverse-direction counterpart (architect enrichment)

> **P6 mirror (architect-verified 2026-10-13):** Phase B's per-pair rollback command (above) **depends on `legacy/agents/<name>/` existing** (Phase A's archive). Reverting Phase A **alone** orphans Phase B's rollback — the per-pair `cp -R legacy/agents/<name> agents/<name>/` becomes a no-op or a failure if `legacy/` is gone. Reverting Phase B **without** also reverting C is fine: the opencode stack still references v2's `innate_skills:["todo",...]` (no loader coupling to opencode post-B's base := v2 copy). The load-bearing reverse-direction rule: **revert in order C → B → A** when undoing the whole mission. Reverting A in isolation when B and/or C are live breaks the B-rollback source-of-truth contract.
>
> **Whole-C-only reverse (companion rule from architecture-recommendation P6):** if a partial C-revert is attempted (e.g., C.2 only without C.1), `daemon/manager.py:78` raises `ImportError` at daemon startup. C must be reverted as a unit; after that, B and A revert in order.

## Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | FAKE_HOME fence not honored — script touches `$HOME`-derived install dir → live install deletion (2026-10-12 incident pattern) | **Critical** (data loss) | Low (fence is bound) | Step 1 sets `FAKE_HOME=$(mktemp -d -t agent-base-v2-XXXXXX)` + trap. Phase B's writes target worktree-anchored paths only; no `$HOME` resolution. |
| 2 | base dir accidentally `rm -rf`'d instead of surgical-delete — risk to `registry.py:918` base-id resolution | High (runtime registry broken) | Low (D-B1 + Step 6 surgical op uses `rm -f` per file, never `rm -rf <base-dir>`) | Step 6 writes files individually; base dir remains intact throughout. Step 8 verifies scope-check only lists per-file changes for the 5 dirs, never dir deletions. |
| 3 | Test pin update misses a site — CI green locally but fails elsewhere | High (CI block at PR time) | Medium (pin sites drift over time) | Step 11 re-greps tests/ at execution time, applies edits, then re-greps again. Pre/post edit grep pair confirms full coverage. New pin sites must be added to the table above (executor updates the table inline in evidence/phaseB/test-pin-sites.txt). |
| 4 | Skill seeding double-seeds when daemon restarts after copy — version-equal no-op FAIL | Medium (daemon behavior side effect, not correctness) | Low (skill_seed_service.py:436 same-version is idempotent; v2 was likely already seeded, base now == v2 means same version) | Step 12 documents and queries the skill bank state. Same-version templates are skipped (no-op). Risk is mainly if v2 was NOT pre-seeded — then post-promote a fresh seed of base == v2 happens once, which is correct behavior. |
| 5 | Per-pair partial copy leaves base in inconsistent state | High (CI failure, runtime may-keep going) | Low (per-pair staged; D-B4 mandate; Step 6 verification per-pair) | Step 6 verifies each pair immediately after copy; failure to verify stops the rollout; per-pair rollback uses legacy/. Phase B DoD requires all 5 verified before green. |
| 6 | Drift between v2 and legacy/ captured state — base ends up byte-different from v2 | Medium (loss of fresh-user parity acceptance criterion) | Very Low (cp -R + sha256 diff = deterministic) | Step 6 diff is sha256 byte-level; any mismatch is detected and blocks Phase B completion. |
| 7 | `cp -p` mtime preservation triggers git spurious modifications — files show "modified" in git status when their content is identical | Low (CI noise) | Low (cp -p on Darwin mtime ok; on Linux mtime may shift if filesystem timestamps changed) | Acceptable: the content changes are real (REPLACE rows); git status will show them as content changes, not just timestamp changes. Use git diff --stat to confirm content changes are visible. If git status shows ONLY timestamp changes for an unchanged-content file, use `git diff --raw` (or `git diff --no-renames --raw | awk '{print $5,$6}'`) to confirm content vs metadata. |
| 8 | meta.json schema drift — v2 has new fields (`skill_injection`, `context_injection`) that some validator (`daemon/registry.py:1207-1225 validate_tool_configs()`) may flag | Medium (daemon warnings on startup) | Low (skill_injection is opt-in; validate_tool_configs warns but does not fail) | Step 8's scope-checks + Step 11's test edit updates catch any new validator-fail surface; warnings are documented in `daemon/registry.py:1207-1225` log pathway but do not block startup. |
| 9 | Test pin update introduces its own pin drift — pin "2.0.0" but the test path expects a different version of a different agent | Medium | Medium (test order / interaction complexity) | Apply pin edits per `evidence/phaseB/test-pin-sites.txt` line-by-line; run the affected tests (`pytest tests/test_agents_api.py tests/test_registry.py tests/conftest.py tests/test_innate_skills_refactoring.py -x`) immediately after the edit to catch breakages. |
| 10 | Dual base↔v2 duplication burden — drift over time as both evolve | Low (architecture debt, NOT a Phase B blocker per user clarification) | High (operational reality over the coming months) | Mission user clarification says: "duplication/drift maintenance implications: NOTE in the plan, do NOT solve". A risk entry + an Open Question at the end satisfies the mandate. Drift acceptance is deferred to a future phase. |
| **15** | **phaseB pin-list misattribution** (R9, architect-verified 2026-10-13) — the prior "~12 meta pin test files" list was largely misattributed; the real base-agent pin set is concentrated in `tests/test_innate_skills_refactoring.py` (`:59-65, :227, :271, :299`). The prior list led the executor toward harmful edits (`tests/conftest.py:443` is the `sample_health_response_data` HealthResponse/daemon-version fixture — flipping it to `"2.0.0"` ACTIVELY BREAKS the test's semantics) or chasing nonexistent lines (`tests/test_agents_api.py:443` — file is 383 lines; `tests/test_registry.py:871` is a synthetic leader fixture, leader not in migration set) | Medium (test breakage or false STOP at gate) | High (without R9 fix) → Low (with R9 fix) | **R9 (executed in this plan revision):** rewrite the pin list to the 3 real sites in `test_innate_skills_refactoring.py` + explicit exclusion warning for `conftest.py:443` + de-listed non-pins table. Step 11 re-greps for the 3 real sites and includes a negative-check confirming `conftest.py:443` is unchanged. |

## Definition of Done

Phase B is DONE when **ALL** of the following are true (each checkable):

- [ ] **DoD-B1** (D4 proof): `evidence/phaseB/diffs/{approver,developer,planner,reviewer,tidier}.v2-before-vs-v2-after.diff.txt` are all 0 bytes — v2 trees unchanged.
- [ ] **DoD-B2** (D1 + D3 proof): `evidence/phaseB/diffs/{approver,developer,planner,reviewer,tidier}.base-vs-v2.diff.txt` are all 0 bytes — base == v2 byte-identical.
- [ ] **DoD-B3** (per-pair filelist match): `evidence/phaseB/diffs/{approver,developer,planner,reviewer,tidier}.filelist.diff.txt` are all 0 bytes.
- [ ] **DoD-B4** (scope isolation): `evidence/phaseB/scope-checks.txt` shows `PASS: v2 untouched`, `PASS: infra dirs untouched`, and the only dirty entries under `agents/` are exactly the 5 base dirs (the unmerged files inside each).
- [ ] **DoD-B5** (test pin updates): `evidence/phaseB/test-pin-sites.post-edit.txt` is empty (no remaining 1.0.0/opencode pin references in tests/).
- [ ] **DoD-B6** (release-tools-shaped digest): `evidence/phaseB/agents-tree.5pairs.sha256` is 64 hex chars; `evidence/phaseB/SUMMARY.txt` reports all 10 diff sizes = 0 bytes.
- [ ] **DoD-B7** (other base agents untouched): `git status --porcelain -- agents/` (filtered to exclude `agents/{approver,developer,planner,reviewer,tidier}/` and the 5 `:(literal)agents/<name>[v2]` pathspecs) lists zero entries under `agents/<other>/`.
- [ ] **DoD-B8** (no v2 path modified): `git status --porcelain -- ':(literal)agents/developer[v2]' ':(literal)agents/reviewer[v2]' ':(literal)agents/tidier[v2]' ':(literal)agents/approver[v2]' ':(literal)agents/planner[v2]'` is empty. *(W3 fix: `'agents/[a-z]*[v2]'` is a character class — VACUOUS; use 5 literal pathspecs. The sha256 manifests remain the PRIMARY zero-touch proof.)*
- [ ] **DoD-B9** (live install untouched): no daemon restart, no DB write, no promote, no install-dir touches. Verifiable by absence of any non-worktree activity in executor's session log + `find /home/nea/ensemble-src -newer /tmp/agent-base-v2-phase-b-start -type f 2>/dev/null | head` doesn't show install-tree files.
- [ ] **DoD-B10** (skill bank audit recorded): `evidence/phaseB/skill-bank-audit.txt` exists, non-empty, contains the v2 version table.

## P5 — Informational note on release manifest scope (architect enrichment)

> **P5 (architect-verified 2026-10-13):** the release manifest is **content-only** (`scripts/upgrade/lib.sh:1928` `_tree_manifest` walks `find . -type f | sort` and emits `<sha256>  <relpath>` lines — no modes, no mtimes, no symlink resolution). Phase A's archive uses `cp -pR` which preserves modes/mtimes, but **nothing in the manifest verifies them**. Equivalently: **"content identity ≠ proven-by-modes"** — a future maintainer reading the manifest must not over-trust it as a mode/timestamp proof. The DoD's per-file `diff -r` + sha256 verification closes the content hole; mode/mtime drift, if introduced, would be invisible to the manifest. If mode-level fidelity ever becomes load-bearing, a follow-up amendment would extend `_tree_manifest` to include `<mode>  <sha256>  <relpath>` lines. (Not needed for this mission; recorded for future-proofing.)

## Cross-Phase Hand-off

- **Phase C** (wiring excision): can now safely edit `daemon/manager.py` etc. — base agents are now skill-injection capable, removing the runtime need for opencode-path codepaths. Phase C does not touch `agents/<name>/` content (Phase B already replaced it).
- **Phase D** (doc references): rewrites any references to "approver/developer/planner/reviewer/tidier - the opencode-era versions of..." pointing to either base (now same as v2) or legacy/ archives.
- **Phase E** (verification evidence pack): consumes ALL of Phase A's + Phase B's evidence under `.agents/shared/planning/agent-base-v2/evidence/` into a single whole-mission pack, then re-runs `diff -r` against live state at mission end to confirm reproducibility.

## Open Questions (resolve before or during execution)

1. **D-B3 test pin list completeness** — Step 11's pre-edit grep at execution time is the authoritative source. Any pin site the planner missed gets caught and added to the executable's edit set. If a NEW pin site has non-obvious post-copy values (e.g., a custom mock with bespoke version), executor must consult v2 source + ask user before editing.
2. **Drift-maintenance implications** (user clarification: "NOTE in the plan, do NOT solve") — documented as Risk #10. A future phase (post-v0.20.1) could retire the v2 dirs entirely; until then, base + v2 must stay byte-equal-or-near. Cargo-cult this rule into the next promote checklist.
3. **Memory.md wrinkle (D3)** — `developer[v2]/memory.md` and `reviewer[v2]/memory.md` carry v2 internal-contract prose that travels AS-IS into base. This is intentional per D3; if user prefers a redaction pass post-promote, that's a separate (defeatable) pass.
4. **Skill bank idempotency at user promote** — Step 12's audit is partial (worktree-readonly). At promote time, live DB state is the truth; same-version no-op (skill_seed_service.py:436) is the safety net.

## Cross-Reference

- **Phase A (legacy-archive)** ↔ Phase B: Phase A's `legacy/agents/<name>/` is Phase B's rollback source. Phase B's per-pair diff = empty is the FRESH-USER PARITY acceptance criterion; Phase A's per-pair diff = empty is the BYTE-IDENTICAL-TO-PRE-OVERWRITE acceptance criterion for archival fidelity.
- **Phase C (opencode-excision)**: runs after Phase B; does not edit base agent content (Phase B owns that surface).
- **Phase D (references-docs)**: runs after Phase B; updates docs that reference the 5 base agents or opencode sources.
- **Phase E (verification-evidence)**: synthesizes Phase A + Phase B evidence; reduces redundancy; produces a single pack for the governor reviewer to approve.
