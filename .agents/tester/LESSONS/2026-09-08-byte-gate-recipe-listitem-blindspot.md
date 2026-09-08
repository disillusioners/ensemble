# Byte-Gate Metrology: diff-recipe greps are blind to markdown list-item lines

**Date:** 2026-09-08
**Arc:** fix/prompt-pure-section-refs final verification (RESULTS/2026-09-08-pure-section-refs-final-verification.md)

## Root Cause
The ratified §5.3a byte-recipe counts added/removed content via `git diff -U0 | grep -E '^\+[^+]'` / `'^\-[^-]'`. Diff lines whose **content itself starts with `+` or `-`** (markdown list items, e.g. `- Need skill-specific test execution…`, `-- >=2 fresh wt.active rows…`) are skipped by those character classes: the removal scores **zero bytes** while any replacement line scores full bytes — producing phantom positive net deltas.

## Impact (measured)
- 9 budget files fd582efd→23defaa3: recipe said **+516 B**; blob truth (`git cat-file -s` after−before) is **−132 B**. 648 B of invisible removals across 5 lines in 2 files.
- The ratified −139 B figure itself only reproduces under blob semantics (−129 at the ratification snapshot commit 27f023ea; the recipe reads +518 even there — so the ruling's number cannot have come from the recipe).

## Fix / Rule
- **Canonical byte-gate method: blob sizes** — `git cat-file -s "<rev>:<path>"`, net = Σ(after − before). Endpoint-exact, additivity-clean across ranges, immune to content-initial `+`/`-`.
- Cross-check identity when both are needed: `blob_net = recipe_net + missed_add − missed_rem`, where missed_* are content-initial `+`/`-` lines the greps skip (`grep -E '^\+[-+]'` / `'^\-[-+]'` finds them).
- Diff-recipes remain usable only on surfaces with no list-item edits; per-commit recipe nets are non-additive across a range (intermediate churn) — only blob endpoints sum cleanly.

## Secondary Lessons (same arc)
- **Verify stated dependency directions empirically, not by ancestry presence.** Task premise said fixes-commit depends on gate-commit; reality was inverted (gate 530c5b6d requires fixes 03eb1398 beneath it — widened gate matches 11/12 pre-fix forms; old gate matches 0/12 new sites). Always run old-detector-vs-new-sites and new-detector-vs-old-corpus before quoting a dependency mechanism.
- **skills-template is NOT composed in daemon/loader.py.** Real chain: `daemon/services/skill_seed_service.py:323,336` (file → skill_bank) → `daemon/services/context_messages.py:841-848,675-676` (verbatim auto-load HumanMessage). loader.py handles soul/skill/workflow/rule/memory only.
