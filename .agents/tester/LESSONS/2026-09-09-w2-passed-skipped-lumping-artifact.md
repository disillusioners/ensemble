# Passed/Skipped Lumping Artifact in Gate Reports (W2 gate, 2026-09-09)

**Context:** W2 maintenancer gate (diff `28efe5c4..2f17e823`). The affected-existing pack
(`tests/unit/test_report_integrity_prompts.py`) returned `50 passed, 17 skipped` while the
W1 RESULTS table had recorded `67 passed / 0 skipped` — an apparent pass→skip regression
of 17 nodes.

**Root cause:** The W1 summary lumped design-conditional parametrize skips into the pass
count. The 17 skips are parametrize-level conditionals (10× "agent has no team_members —
not a parent agent", :242; 7× "predates Wave 1 (grandfathered parent)", :244) whose
predicates read OTHER agents' metas. The W2 diff provably touches nothing outside
`agents/maintenancer/` + `tests/` (verified independently by two workers), so the skip
predicates could not have changed between 28efe5c4 and 2f17e823 — the skips were always
there; W1's row was a reporting artifact (67 = 50P + 17S lumped).

**Rule:** Gate summaries must always report passed / failed / skipped separately
(ground-truth `--collect-only` for collected). A "N/N PASS" line over a suite with
conditional skips is ambiguous and manufactures false regressions (or false clean bills)
on the next diff. When a pass↔skip delta appears with 0 failures, FIRST check whether the
skip predicates depend on files the diff touches; if not, it is a prior-reporting artifact
— correct the record, don't burn a base-worktree adjudication on it.

**Detection shortcut:** `pytest -rs` (skip reasons) on the suspect file — design-skips
carry stable, parametrize-level reason strings distinct from environment skips.
