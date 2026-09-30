# Lesson: verifying agent skill-config changes in worktrees (designer-skill-injection-fix, 2026-09-30)

## Context
Independent verification of a 2-commit agent-config change in a git worktree (no daemon boot allowed). All 7 items PASS; 96 tests green (67+29). Full evidence: RESULTS/2026-09-30-designer-skill-injection-fix-verification.md.

## Reusable findings

1. **Fast runnable suite for skill-config verification:** `tests/unit/test_skill_seeding.py` — 29 tests, 1.27s, in-memory SQLite + tmp-dir manifests, no boot/DB/network. It exercises the seeding scan contract (`agents/*/skill-set.yaml` + `skills-template/`). Use it (plus static trace) whenever an agent's skill-set/meta changes; no boot needed.

2. **Worktree pytest import guard that works:** worktree has no `.venv`; main `/home/nea/ensemble-src/.venv` editable `.pth` pins `import daemon` to the MAIN checkout. `env PYTHONPATH=<worktree> ENSEMBLE_SELF_ENV=dev /home/nea/ensemble-src/.venv/bin/python -m pytest …` — PYTHONPATH reliably precedes the `.pth` (verified twice via `daemon.__file__` printing the worktree path). No `uv sync` fallback needed.

3. **Reference-fact refinements (supersedes loose line refs):**
   - `registry.py` = flat `daemon/registry.py` (NOT under repositories/). Fail-closed default: `:298-301` `Field(default=False)`; mapping sites `:623` and `:688` `meta.get("skill_injection", False)`.
   - `daemon/manager.py:6601-6646` is the DDL/migration block whose COMMENTS state the auto_load propagation contract (skill-set.yaml → skill_bank → clone-side skills). Behavioral legs: `skill_seed_service.seed_all` (scan; `_MANIFEST_FILENAME="skill-set.yaml"` :252, agents/ iterdir :274, templates_dir :348) + `_build_auto_load_block` (`daemon/services/context_messages.py:1486`, call sites :1982/:2179, independence comment :2148-2154).
   - `_build_auto_load_block` is independent of `skill_injection` — own-skill must-load survives injection=false. This is the exact developer[v2]/tester/designer pattern.

4. **Coverage gap worth a future (non-verification) task:** agent-structure mirror tests exist only for maintenancer/worker/reviewer-v2/project-manager — no designer sibling test. Adding one would pin the designer config against regressions.

5. **"One site" greps must separate source vs docs:** repo-wide `"Failed to replenish"` has exactly 1 source site (warmup_pool.py:452) but also 1 hit in `.agents/shared/planning/mcp-server-pool/phase1-plan.md:167` (historical planning doc quoting the pre-demote warning form). Filter by path class before declaring uniqueness.
