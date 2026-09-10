# W3 Gate: brief numeric claims vs blob-state, and dispatcher path typos

**Date:** 2026-09-09 · **Gate:** W3 maintenancer final pre-merge (RESULTS/2026-09-09-w3-maintenancer-final-gate.md)

## 1. Task-brief numeric claims are lineage claims — verify against blob state, never against the brief

The W3 gate brief carried two numeric claims that did not match any measurable state at HEAD `012de252`:

- **"memory.md INDEX 921B"** — whole file is 4,898B (blob-verified committed state, zero drift); the INDEX block (heading L2 → L14) is **918B**; no reading yields exactly 921 (Δ3 = measurement-boundary artifact; table-only L4-9 = 706B, L1-9 = 770B).
- **"17 files"** in agents/maintenancer/ — the W1-era KV post-merge snapshot (10 P1 + 6 knowledge + kb-curator); actual at W3 HEAD = **25** (W3-P5 added README + ROLLOUT + 6 skills-template).

Both were **claim imprecision, not code defects** — the substance behind each claim (INDEX content 6/6 + rider-b triggers; file inventory shape) verified green, and no test asserted either number.

**Rule:** when a caller/task brief cites exact byte counts or file counts, treat them as *approximate lineage claims* (usually measured at an earlier commit or a different block boundary). Dispatch verification against the blob (`wc -c` on the exact block, `git show HEAD:<path> | wc -c`, find|wc -l) and adjudicate the delta explicitly — never fail a gate on a brief-number mismatch alone, and never silently "pass" a number nobody could reproduce; record both figures.

## 2. Dispatcher path typos are cheap to catch — make workers verify path existence before invocation

This gate's p-affected dispatch cited `tests/unit/tools/test_report_integrity_prompts.py`; the real path is `tests/unit/test_report_integrity_prompts.py` (no `tools/` segment). The worker caught it via `find`/`ls` existence-check before invoking pytest — zero scope damage.

**Rule (already emerging in prior gates):** every pack dispatch should instruct the worker to verify the named file path exists (and `--collect-only` matches the expected count) BEFORE the timed run. A path-verification step converts a dispatcher typo from a silent scope-shift into a 5-second self-correction. Ground-truth-collect-first (used in all three maintenancer gates) already provides this for free when the expected count is stated.

## 3. Minor: AgentRegistry API shape for census probes

`AgentRegistry(Path(...))` only constructs; `discover()` populates and returns None; enumerate via `list_all()` (untagged) / `list_all_grouped()` (untagged+tagged) / `get(id)`. 31 untagged / 36 total incl. `[v2]` tags. W1's trap note (str not auto-wrapped to Path) still holds at 012de252.
