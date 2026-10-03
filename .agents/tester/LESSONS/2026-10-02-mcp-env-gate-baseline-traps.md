# Lessons — MCP env gate 2026-10-02 (97b7e665)

## 1. Baseline-depth trap: --collect-only "0 errors" says NOTHING about fixture health
W1's collect-only inventory showed `test_builtin_mcp_servers.py` = "86 collected, ZERO errors" → I framed any error as NEW. Wrong: fixture ERRORs only surface at SETUP time, never at collection. The file actually yields 69P/17E when RUN. Consequence: an interim worker report called 17 errors "NEW" and a base-comparison leg was burned to discover they were the mission's own known pre-existing family.
**Rule:** "pre-existing reds" baselines must come from EXECUTED legs (or KB/prior RESULTS), never from collection-only output. Collection validates importability + counts, nothing more.

## 2. Worktree import-proof refinement: run it with cwd INSIDE the worktree
The editable-install trap doc says verify `import daemon` resolves in the worktree. Refinement found by g2-basecmp-shardD: with `python -m`/`-c`, sys.path[0] = cwd (`''`). Running the proof from the MAIN checkout cwd resolves `daemon` to the main checkout EVEN WITH `PYTHONPATH=<worktree>` set — cwd wins, PYTHONPATH does not override position 0.
**Rule:** `cd <worktree> && PYTHONPATH=<worktree> <python> -c "import daemon; print(daemon.__file__)"` — proof executed from inside the worktree, or it proves nothing. (All four basecmp legs in this gate ultimately complied; the shard-D leg initially tripped this.)

## 3. Registered tools_suite pack is chronically over-scoped vs its own self-cap
`test/packs/tools_suite_unit_test.sh` runs the whole tests/unit/tools/ dir (3284 collected across 57 files) with a 110s internal cap. Observed pace ~6.9 tests/s → ~8 min required. It can NEVER pass as registered; historical "3138P" baselines must have come from a different invocation shape.
**Rule:** Test Architecture Fix commission owed — split the registered pack (or raise internal cap to a 5-min-compliant 280s and slim scope). Interim workaround that produced full coverage this gate: 4 disjoint ad-hoc shards (A 1377 / B 685 / C 759 / D 386) + 3 already-covered files, disjointness + count arithmetic proven by the inventory worker.
CORRECTION (2026-10-03): an earlier version of this lesson claimed the pack's quarantine deselect list carried a misspelled node name — FALSE. The pack (and its 2 sibling pack scripts) carry the correct `test_access_archive_path_traversal_rejected` since 68823b49. The misspelling (`test_access_path_traversal_rejected`) entered via my OWN dispatch chain: an inventory worker relayed the deselect list as "verbatim" with one name misspelled, and I propagated it into ad-hoc shard dispatches without grep-verifying against the source script. Consequence: 1 of 5 deselected nodes silently ran in shard C (pytest reports "4 deselected", not "5") and its known-quarantined failure surfaced as an unattributed red, burning a base-comparison leg.
**Rule (new):** treat any worker-relayed "verbatim" list as UNVERIFIED — grep the source artifact yourself (or re-task a worker to) before dispatch; assert the pytest "N deselected" line equals the expected N.

## 4. Known-red census for tests/unit/tools/ at latest-lineage commits (stable, base-attributed 2026-10-02 @ 24705dc4)
- prompt-lint ×3: `test_no_bare_md_filename_tokens_in_prompts[workflow.md11|memory.md7|workflow.md17]` (agents/developer + developer[v2] bare .md tokens)
- knowledge ×2: `TestExploreCallerModelOverrides` model-kwarg forwarding (stale vs 1a40bc56, KB-documented since v0.16.0)
- archive ×1: `TestAccessMemoryArchive::test_access_archive_path_traversal_rejected` ("access denied" vs expected "not found" — test-debt)
- watch ×2: `test_watch_job_mission_terminal.py` :203 (AsyncMock-vs-int leak) + :315 (ungated watcher claim) — v0.16.0 "watch×2" family
- builtin-17: `tests/unit/test_builtin_mcp_servers.py` fixture mock-config gap (`services.service_tool` missing from spec'd Mock, dies at manager.py:746)
All reproduced byte-identically on base 24705dc4 in isolated worktrees (import-proved). Treat as pre-existing in future gates; QUARANTINE.md rows added 2026-10-02.

## 5. KMS-writer porcelain is run OUTPUT, not contamination
install-audit.jsonl accumulates `+N` lines per KMS-touching shard (kms-family +14, tools shards +12/+9...). Never clean, never commit; report as expected delta. Ambient dirt at gate start may predate your runs — timestamp the before-state.
