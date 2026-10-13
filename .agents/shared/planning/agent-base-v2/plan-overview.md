# agent-base-v2 Migration — Plan Overview

- **Date:** 2026-10-13 (planning complete; architectural enrichment folded same day)
- **Branch / base:** `feature/agent-base-v2` @ `885a19d4f45a3403a1bc615ec0f393630c938c84` (latest)
- **Worktree (ALL work):** `/home/nea/ensemble-src-wt-agent-base-v2` — main checkout is dirty and untouched
- **Status:** Planning COMPLETE + architecturally VALIDATED + review-hardened through approver iteration 002 CONVERGENCE (planner-verified). All five phase plans finalized with: exploration addendum, architecture fold pass (R1–R10 + P1–P8), fix pass 1 (C-1/C-2 + W1–W6 + S1–S8), fix pass 2 (B1–B4 + N1–N9), and the convergence pass (002-B1..B4 + carried notes + two planner-driven disposition rounds). **R10 FINAL TESTER GATE verified by independent planner execution: 48-pathspec exclusion set → 1,843 hits / 98 files at HEAD → 26 MOVES + 72 TOUCH + 0 un-dispositioned residue**; negative-guard and vendored-snapshot allowlist classes documented as explicit semantics. Whole-mission DoD = 18 criteria. Executor-ready.
- **Planners:** 3 parallel plan-creation workers (phaseAB / phaseC / phaseDE) + planner synthesis (this file). Validation: architecture-recommendation.md (3 skill-equipped architect workers, all cites re-grepped in worktree).

## Mission (binding)

1. **Base := v2.** Promote agent v2 to THE base agents. KEEP BOTH base and v2. The 5 untagged base dirs (`developer`, `reviewer`, `tidier`, `approver`, `planner` — the explicit five; NO leader pair exists) get content REPLACED by byte-identical copies of the v2 dirs. v2 untouched, still THE live path — existing users and the ensemble's own operation must notice NOTHING (proven via before/after checksums).
2. **Full opencode-stack retirement (final scope).** "Remove the opencode stack (related modules) in ensemble and backup to legacy." ENTIRE stack — comm lane, session/API coding-worker lane, shared modules in `daemon/opencode/`, InstanceManager-coordinated OpenCode resources, MCP configs (none exist — verified), agent-facing surfaces — removed from the active codebase, backed up to root-level `legacy/` (copy-then-verify, preserved but unused).
3. **Opencode-free active tree (R10 binding gate).** Post-migration, ANY active-agent-surface opencode reference is a DEFECT → FIX, not a note. Only survivors: the legacy archive itself + historical/audit material (justified per-row in the tester allowlist). Tester grep-verifies as a GATE.
4. **Acceptance:** fresh user on base == user on v2; v2 proven byte-identical before/after; ensemble's own system unregressed.

## Authoritative verdicts (exploration addendum 2026-10-13)

- **VERDICT 1 — one retirement unit, two named roles.** `daemon/opencode/` is a single package (port of the opencode_skill Go binary); the comm service is the protocol layer inside the session lane (`server.py`; sole consumer `external_opencode.py:20-21`); single import graph / SQLite store / manager init (`manager.py:1427-1436`); `__init__.py:13` router claim STALE.
- **VERDICT 2 — no live dependency → FULL-STACK REMOVAL.** Coder opencode-free by design (contamination guards in `tests/unit/test_coder_agent.py:144,168,369+,510,253` keep passing); opencode innate skill only in the 5 retiring base metas; zero tools.allow entries; runtime dead; no MCP configs; port 4095 idle; `~/.opencode_skill`/`~/.opencode` absent. `keyword_extraction.py` + `context_injection.py` live-shared → STAY (modules per D5; wording per R10).
- **ORDERING CONSTRAINT (D-A8).** Stack removal lands WITH or AFTER Phase B, never before (innate-skill loader: `daemon/loader.py` — R4-corrected cite; warning path `:389`). Phase A pure-copy safe anytime.
- **Architect validation (fold pass).** Load-bearing properties VALIDATED: v2 zero-touch, migrations append-only (`daemon/migrations/` zero opencode hits), `legacy/` scope isolation, frozen-binary neutrality, git-only reversibility, D8 no-promote. Loader coupling graceful (`.get()` default + logger.warning), not fatal.

## Phases

| Phase | Plan file (this dir) | Objective | Hard gates |
|---|---|---|---|
| A | `phaseA-legacy-archive.md` (845 ln) | Copy-then-verify archive (sha256, `shasum -a 256`, lib.sh-compatible) of the 5 opencode-era base contents + full stack (16 MOVE rows incl. `example/SKILL.md` STACK-11) into `legacy/` mirroring source shape; before/after map, three terminal states, `retained-live` EMPTY | DoD-A1..A10; verdict gate; anomaly → halt + user |
| B | `phaseB-base-copy.md` (781 ln) | Base := v2 for the explicit five pairs; per-file base==v2 manifest diff EMPTY; v2 zero-touch proofs; **R9-corrected pin updates** (real pins: `test_innate_skills_refactoring.py:59-65,:227,:271,:299`; ⛔ `tests/conftest.py:443` HealthResponse fixture EXCLUDED — editing breaks it); retires the 5 opencode innate declarations | base==v2 proof; `git status --porcelain -- 'agents/*[v2]*'` empty; conftest negative-check |
| C | `phaseC-opencode-excision.md` (1007 ln) | 5-commit severance/removal: C.1 wiring severance → **Step 1.5 BLOCKING GATE** → C.2 stack removal → C.3 bulk move → C.4 TOUCH edits (now incl. R1 `test_tool_filter.py`, R2 full `test_jsonb_migration.py` surface, R3 `testpaths`, R5 Makefile comment, P1/P2/P3, R10 mandatory rephrase set) → C.5 boot proof | **R6 per-commit invariants:** C.1 boot-green only, C.2 boot-green only, C.3 move-only/no invariant, **C.4 = FIRST test-green commit**; R10: all rephrases mandatory (optional framing superseded) |
| D | `phaseD-references-docs.md` (374 ln) | Docs refresh (agents.md §2, skill-evolution.md:174, README/AGENTS/writing-guide §9, testing-guide.md, CHANGELOG `[Unreleased]`); docs-consistency sweep with **R10-aware dispositions**: MANDATORY FIX for every active-tree doc row; only explicit-allowlist rows survive as "leave" | P4 stale-history rows routed through the allowlist with justification |
| E | `phaseE-verification-evidence.md` (1081 ln) | Evidence pack under `evidence/`: v2 before/after manifests, base==v2 diffs, legacy re-verify, full test wall (R9 pin set), fenced boot-scan, default_agent_versions GET, skill-seed no-op, before/after map, 7-surface residual audit, **Step 13 FINAL TESTER GATE (R10)** | **R8 capture-before-boot** (inner_soul `daemon/tools/inner_soul.py:1043,:1107-1176` writes base `memory.md` at runtime — equality proof BEFORE any boot); **R10 gate:** `git grep -niE 'opencode'` + exclusion set → zero matches — exclusion set includes historical/audit material per fix-pass W4 (`legacy/`, `.agents/shared/planning/**`, `.agents/*/RESULTS*|LESSONS*`, `.agents/tester/PACKS.md`, `.kilo`, CHANGELOG, docs/incidents, backup/, vendored opencode-ai, docs/mcp-kb-usage.md, conditional docs/bugs); `evidence/r10-allowlist.md` one row per exclusion + justification |

**Execution order:** A → B → (Step 1.5 gate) → C → D → E; E's Step 13 gate runs LAST. Ordering constraint binds C to B. R8 binds E's proof ordering (diff+manifests before boots).

## Architectural enrichment (fold pass 2026-10-13 — source: `architecture-recommendation.md`)

Folded REQUIRED amendments: **R1** `test_tool_filter.py` 3 red tests (:710-716,:750-764,:827-860 → C.4 TOUCH) · **R2** `test_jsonb_migration.py` full-file surface (:68-69,:75,:106-107,:194-197,:223-226,:366-367,:497,:518; :75 breaks collection post-C.2) · **R3** `testpaths=["tests"]` in pyproject.toml (mechanism adjudicated: testpaths NOT norecursedirs) · **R4** C.1/C.2 prose contradiction resolved (edit lists authoritative; Step 1.5 rationale → prompt-dir removal; §6.4 four sites; loader cite → `daemon/loader.py`) · **R5** Makefile :173-174 comment + :186 FILES (own gate fails otherwise) · **R6** C.3 zero-grep gate moved to C.4 + per-commit invariants declared · **R7** pair lists fixed to the explicit five (leader NOT in set; `agents/leader[v2]` nonexistent; attestation-leader refs are name pins, unrelated) · **R8** capture-before-boot (above) · **R9** pin list corrected (above). **R10** opencode-free active-tree GATE (binding user directive): REPORT→FIX semantics superseded for all agent surfaces; mandatory-fix set (soul/knowledge one-liners, 9 daemon comment/docstring sites, Makefile/pyproject/.env.example wording, Phase D active-doc rows, former-P7 stragglers); gate mechanics verbatim in phaseE Step 13; boundary cases: mcp-kb-usage.md (strictly-upstream verify → allowlist), `plugins/opendesign/copy_freely/design-systems/opencode-ai/**` (vendored third-party, payload-shipped via stage.sh:407 — verify+allowlist, NOT a legacy move), `.gitignore` opencode lines (intentional-keep for D7), `.kilo/` (executor-classified at gate time); §11.5 load-bearing check PASSED (all five v2 trees grep-zero → gate satisfiable without D4 violation); §11.7 interplay: gate greps COMMITTED tree, R8 governs equality proof — independent, both stand. Recommended P1–P8: **all accepted** (P7 superseded to mandatory by R10); P6 = Phase C reverts as a UNIT.

## Risk register

| # | Risk | Mitigation (owner phase) |
|---|---|---|
| 1 | Tier-1 excision ordering vs. boot | C's C.1–C.5 bootable checkpoints; C.5 boot-proof artifact |
| 2 | Base dir deletion (registry requires base dirs exist) | B replaces content in-place; no delete/rename; name pins verify-only |
| 3 | v2 `memory.md` dual-contract prose | D3: travels as-is; noted (sharpened by R8: base memory.md is runtime-mutable — capture ordering covers this mission) |
| 4 | Skill-seed double-seed after copy | E verifies version-equal no-op |
| 5 | `default_agent_versions` runtime value | E: single read-only GET pre/post, unchanged |
| 6 | Hardcoded agent-name pins | Safe by construction (no renames); verify-only |
| 7 | Real-tree test wall | Full pack in E; R9 pin set in B; R1/R2/P1 red-window managed by R6 invariants |
| 8 | Release manifest machinery | Self-adapting; integrity_verify reads staged tree only |
| 9 | Ordering-constraint violation (stack removed before B) | Step 1.5 BLOCKING GATE + E DoD #12 commit-graph check |
| 10 | Executor evidence contradicts VERDICT 2 | NOT silent: new `retained-live` row + USER DECISION ITEM (O6) |
| 11 | 🔴 `test_tool_filter.py` 3 tests red at C.1 | R1 (phaseC) |
| 12 | 🔴 `test_jsonb_migration.py` collection ImportError post-C.2; surface beyond :70-107 | R2 + R6 invariants (phaseC) |
| 13 | 🟡 Bare `pytest` post-C.3 collects `legacy/tests/**` → ImportError storm | R3 testpaths (phaseC) |
| 14 | 🟡 Phase E proof invalidated by inner_soul runtime writes to base `memory.md` | R8 capture-before-boot (phaseE) |
| 15 | 🟡 Pin-list misattribution → harmful edits (conftest HealthResponse) or nonexistent-line chases | R9 + negative-check (phaseB/E) |
| 16 | 🟡 C.2 prose contradiction misleads executor | R4 (phaseC) |
| 17 | 🟡 Stale pair list (`leader[v2]` absent) → false STOP at gate | R7 (phaseC/B) |
| 18 | 🟢 C-revert non-atomicity (partial revert → `manager.py:78` ImportError) | P6: whole-C-only revert documented (phaseC) |
| 19 | 🟡 R10 residue: active-tree opencode reference survives as "note" → gate failure | R10 mandatory-fix set + allowlist discipline (phaseD/E Step 13) |

## Rollback

git-only on `feature/agent-base-v2`. Reverse order: D → **C (as a UNIT — C.1↔C.2 mutually broken in partial states)** → B → A last/skippable (additive) — and mirroring the forward note: reverting A alone orphans B/C (broken states), never do it. Whole-mission rollback = fresh branch from `885a19d4f`. No irreversible operation exists (architect Focus-8 ruling: every removal git-tracked; legacy/ additive; runtime DBs gitignored). **No promote (D8)** — zero live exposure.

## Deliverables & repo state

- 5 phase plans + `plan-overview.md` (this file) + `decisions.md` + `architecture-recommendation.md` (enrichment source, read-only) + `evidence/` scaffolding (`TOOL.txt` hash lock-in).
- Line counts post-fold: A 845 · B 781 · C 1007 · D 374 · E 1081 · overview + decisions (this pair).
- Worktree confinement verified 2026-10-13: `git status --porcelain` shows ONLY `?? .agents/shared/planning/agent-base-v2/`; `agents/*[v2]*` clean.
- Planning dir currently untracked — the mission executor commits it with the first mission commit.

## Drift/duplication notes (per binding directive: NOTED, not solved)

- Base = frozen copy of v2 → future v2 edits must be deliberately mirrored.
- Real pin surface concentrated in `test_innate_skills_refactoring.py` (post-R9 truth; conftest.py:443 is NOT a pin and must never be edited).
- Base `memory.md` is runtime-mutable (inner_soul) — beyond the D3 prose wrinkle; future equivalence proofs must account for it.
- `skill-set.yaml` + `skills-template/` duplication doubles the skill-seed surface (no-op today; future skill edits land in both).
- Repo-root stray `opencode_sessions.db`: recorded in evidence, not deleted.

## Open items

None blocking. Single forward-compat mechanism O6 (phaseE): if executor verification contradicts VERDICT 2 → new `retained-live` row + USER DECISION ITEM filed, never silent.
