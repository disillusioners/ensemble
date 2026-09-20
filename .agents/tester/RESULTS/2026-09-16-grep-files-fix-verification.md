# Test Report: grep_files recursion fix — INDEPENDENT VERIFICATION (Debug Phase 5)

Date: 2026-09-16
Worker Instance: 2a20d4b4-e9d7-489d-92f9-e16f2cd3e76a (grep-files-repro, revived; no load_skill — verification dispatch)
Branch / commit verified: `fix-grep-files-recursive-include` @ `3fe667fa` ("fix: grep_files include now filters recursively + expands brace alternates") — HEAD at verification time
Prior report: RESULTS/2026-09-16-grep-files-include-recursion-repro.md (repro + red pin)

## Verdict

- **ORIGINAL SYMPTOM GONE: YES** — all 6 original matrix rows PASS (incl. both brace forms in row 2; both controls unchanged).
- **REGRESSIONS: NONE** — independent counts (worker's own runs, dev numbers not reused): pin suite 8/8, family `tests/unit/test_filesystem_*.py` 64/64, `tests/test_tool_filter.py` 55/55.
- **Residual design note (🟢 nice-to-have, documented-as-intentional):** mixed-brace include `{**/*.ts,*.html}` — when the WHOLE pattern contains `**`, no `**/` prepend is applied to bare alternates after expansion, so the `*.html` alternate stays root-level-only. Implementation matches updated docstring (filesystem.py:608-613 "Patterns containing `**` … passed through verbatim"). Not a regression; see Follow-ups.

## Precheck

- Branch = `fix-grep-files-recursive-include`; HEAD = `3fe667fa`. Pin file tracked in that commit, byte-identical to the original pin (250 lines, all 8 tests present).
- Foreign working-tree edits: ` M .agents/tidier/notes.md` (pre-existing) + untracked planning/RESULTS/packs files incl. tester's own RESULTS/LESSONS from the repro phase. None touched; none under daemon/ or tests/ source.

## Row-by-row (original symptom → current behavior)

| Row | Setup | Original symptom | Current | Verdict |
|-----|-------|------------------|---------|---------|
| 1 | `daemon/` + `*.py` + `EXECUTOR_ENV_ALLOWLIST` | false "No matches found" | `daemon/tools/upgrade_journal.py:986` + `:994` hits returned | ✅ PASS |
| 2a | `daemon/` + `{*.py,*.sql}` + `def ` | zero matches (even root) | 20 hits, root + subdir mix | ✅ PASS |
| 2b | `daemon/` + `*.{py,sql}` + `def ` | zero matches | same 20 lines as 2a | ✅ PASS |
| 3 | tmp tree, root 0 `.py`, hits in `inner/`+`inner/deeper/` + `*.py` + `import` | "No matches found" (impossible) | both nested files returned | ✅ PASS |
| 4 | control: no include | worked | still works | ✅ PASS |
| 5 | `daemon/` + `*.py` + `import`, limit 50 | 50 hits, 0 subdir | 52 lines: 45 root + 5 subdir (`daemon/_archived/event_bus.py:17-22`) | ✅ PASS (subdir hits present) |
| 6 | control: non-existent path | proper error | `ERROR: Path does not exist: ...` | ✅ PASS |

## Suite results (own runs; `uv run python -m pytest` from repo root, timeout 300)

| Suite | Count | Time |
|-------|-------|------|
| `tests/unit/test_filesystem_grep_include_recursive_regression.py` | **8 passed, 0 failed** | 0.21s |
| `tests/unit/test_filesystem_*.py` (family) | **64 passed, 0 failed** | 0.33s |
| `tests/test_tool_filter.py` | **55 passed, 0 failed** | 0.23s |

Adjacency: grep for other tests pinning filesystem docstrings/grep_files text found none beyond the pin file (`tests/test_help_tool.py` uses synthetic tools, not real grep_files). No extra files needed.

## Combo spot-check (beyond original suite; inline /tmp probes, no repo writes)

| Combo | Include | Result | Per-alternate expansion? |
|-------|---------|--------|--------------------------|
| A/A2 | `**/*.{ts,html}` | nested `.ts` AND `.html` found at depth 1 and 2; `.js`/`.md` ignored | ✅ YES |
| B/B2 | `{**/*.ts,*.html}` | `.ts` recursive; nested `.html` MISSING (root-only alternate) | ⚠️ PARTIAL — bare alternate not prepended when whole pattern contains `**` |

Behavior trace (B): `_expand_include_glob` tests `"**" not in pattern` on the whole input → no prepend → `_expand_single_level_braces` yields `**/*.ts` + `*.html` → latter globbed root-only. Docstring documents verbatim passthrough for `**`-containing patterns — impl aligns with docs. Design choice, not a defect against documented contract.

## Follow-ups (non-blocking)

- [ ] 🟢 Leader decision: accept documented mixed-brace semantics OR extend fix to per-alternate prepend. If extended, add pin tests for `{**/*.x,*.y}` mixed form (current pin suite's brace cases all lack `**` at whole-pattern level, so they cannot catch this class).
- [ ] Scratch evidence lives in /tmp only (`verify_grep_fix.py`, `combo_spotcheck.py`, outputs) — ephemeral by design; this report is the durable record.

## Artifacts & status

- No repo files added/modified by the worker during verification; no commits/staging (per instruction).
- Tester docs from repro phase remain untracked in working tree (RESULTS/LESSONS) — normal for tester flow; leader may fold into a path-scoped evidence commit if desired.
- Total verification wall time: ~12s.

---

# Addendum: Polish commit f87e3a37 — light re-verify (same day)

Commit under review: `f87e3a37` ("grep_files review follow-up — doc/comment truth + recursion/brace pins") on top of `3fe667fa`, branch `fix-grep-files-recursive-include`. Worker: same instance, light round (~6s wall).

## Zero-logic-change verdict: PASS

- `git diff --stat 3fe667fa..f87e3a37` → exactly 2 files: `daemon/tools/filesystem.py` (+44/−10 production) and the pin suite (+281). No other files.
- Production diff = 4 hunks, ALL docstring-body / pure `#` comment / blank lines. Executable structure byte-identical between 3fe667fa and f87e3a37 (worker enumerated the function bodies line-by-line).
- All 3 purged `byte-identical` occurrences lived in docstrings/comments — none in code-flow string literals. Doc-truth side-benefit: docstrings now honestly document the pre-existing `sorted()` deterministic ordering.
- Pin file: +7 tests only (W3 double-star-verbatim, W4 recursion+brace combo, W5 nested-brace-literal, 2 pagination pins, S3 sorted-order, S5 overlapping-alternate dedupe); original 8 names intact and unmodified — no weakening.

## Suites (own runs)

| Suite | Count |
|-------|-------|
| Pin suite | **15 passed, 0 failed** (0.22s) |
| Family `tests/unit/test_filesystem_*.py` | **71 passed, 0 failed** (0.30s) |
| `tests/test_tool_filter.py` | **55 passed, 0 failed** (0.17s) |

## Spot-check

Row-1 scenario post-polish: `daemon/` + `*.py` + `EXECUTOR_ENV_ALLOWLIST` → nested `daemon/tools/upgrade_journal.py:986,:994` hits returned, unchanged from 3fe667fa. Bonus: relative-path + `workdir=None` still surfaces the proper resolver error (guard intact).

## Final verdict: MERGE-READY (from a testing standpoint)

Full 6-row matrix verification on 3fe667fa remains valid (zero logic change proven); all suites green under own counts; behavior unchanged. No repo writes by tester during this round.

