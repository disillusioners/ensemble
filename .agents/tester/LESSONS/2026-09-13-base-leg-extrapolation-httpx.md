# Base-Leg Extrapolation Error on Composition-Dependent Failure Families (2nd occurrence)

**Date:** 2026-09-13
**Gate:** recovery-ladder P1 verification (RESULTS/2026-09-13-recovery-ladder-p1-verification.md §4 #12)

## Pattern

A base A/B leg observes a failure family ABSENT in its single base run and PRESENT at HEAD, and concludes BRANCH-CAUSED. For failure families that are **suite-composition / order dependent**, a single leg per side cannot support that conclusion: the two legs run different suite compositions (branch adds ~100+ new tests, shifting xdist bucketing and collection order), so the family can churn between legs without any tree difference.

## Concrete case

httpx `TypeError: object.__new__()` family (~50E+5F, `daemon/opencode/client.py:234`): base leg reported "absent at base → ~57 branch-caused". Discrimination probe overturned it: identical venvs (httpx 0.28.1 both), zero branch commits on httpx surfaces, poison (`tests/integration/test_vscode_proxy.py` — monkeypatches `httpx.AsyncClient.__new__` on the shared module singleton) and victim (`tests/opencode/test_client.py`) byte-identical base↔HEAD, and a minimal pair-scope run reproduces byte-identical counts at BOTH commits. = QUARANTINE row 60 documented class. The identical wrong-extrapolation was made and corrected on 2026-08-28 (row-60 history).

## Rule

Before claiming branch-caused for a family that is absent-at-base:
1. Check QUARANTINE.md/PACKS.md for a documented composition/env class matching the signature (rows 35/37/60/72 etc.).
2. Require scope-matched reproduction: minimal file-set (polluter + victim) at BOTH commits — if counts match at both, it is composition-dependent, not tree-caused.
3. Compare venvs (`httpx.__version__` + `__file__`, `uv pip list`) before any env-vs-tree claim; a fresh `uv sync` on one side and a stale `.venv` on the other produces false asymmetry in both directions.

## Bonus (row-60 bisection progress)

`test_vscode_proxy.py` ALONE reproduces the full 43E+5F signature vs `test_client.py` in one process. Remaining: pin the exact polluting test within test_vscode_proxy.py (candidates: `__new__` monkeypatch sites :488/:530/:570, direct module-attr assignments :1852/:2550/:2619 with restore-to-current-value no-op risk).
