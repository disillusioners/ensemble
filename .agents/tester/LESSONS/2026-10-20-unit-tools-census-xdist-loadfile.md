# tests/unit/tools/ census: use `--dist loadfile` — `--dist load` adds a 14th artifact failure (2026-10-20)

Context: ari-tools Phase-1 flag-3 reproduction (13 pre-existing failures in `tests/unit/tools/`, 3810 nodes / 72 files).

## Finding
- `pytest -n 8 --dist load` (default distribution) intermittently fails `test_snapshot_tools.py::TestRegistrationChain::test_loader_warm_list_wires_snapshot_factory` with `KeyError: 'snapshot_create'` — observed 2/2 on the branch, 0/2 on the base tree, 0/1 under `--dist loadfile`, 0/2 serial. Reported as branch-correlated-NOT-caused: `daemon/loader.py:471-487` `_ensure_tool_metadata_populated()` early-returns on a module-global cache guard (`len(_tool_metadata) >= 20`); if an earlier same-worker test populated the cache without the snapshot tools, the full warm scan never runs. Board tools never enter `_tool_metadata` (factory-built, not import-time).
- **Census recipe (deterministic):** `timeout 300 .venv/bin/pytest tests/unit/tools/ -q --tb=line -p no:randomly -n 8 --dist loadfile --junitxml=<path>` → exactly the 13 stable failures. Adjudicate any count mismatch with a serial re-run of ONLY the failing files.
- **Branch-vs-base blame proof (read-only, no repo mutation):** `git archive <base> | tar -x -C /tmp/<dir>` + `PYTHONPATH=/tmp/<dir>` shadows the editable install → base tree runs the identical test ids without touching the worktree.

## Stable 13 (as of 2026-10-20, HEAD 20cd96bca == base 630cefebb)
3× `test_inner_soul_compaction.py::TestUpdateMemoryMdIntegration` · 5× `test_prompt_section_reference_integrity.py` · 2× `test_instance_tools.py` (planner allow-list gaps: subtree_messages/subtree_status) · 2× `test_knowledge_tools.py::TestExploreCallerModelOverrides` · 1× `test_snapshot_tools.py::test_gate_flag_enabled_on_targets` (duplicate developer in snapshot_enabled targets).

## Gotchas banked
- pytest-xdist junit classname→file mapping needed for per-file green extraction.
- Serial runs of the 5 failing files: 1909 nodes, 0 skipped (the census-wide 7 skipped are xdist/no_xdist-conditional).
- Full evidence: `.agents/tester/RESULTS/2026-10-20-ari-tools-phase1-workbook-verification.md` §2 flag 3 + §7.
