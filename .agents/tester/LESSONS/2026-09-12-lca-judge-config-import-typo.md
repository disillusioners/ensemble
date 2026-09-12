# LCA Judge Config Import Typo — `from ..config` (two dots) at graph.py:3429/:3703

Date: 2026-09-12
Found by: `mid_work_report_testcase` demonstration (worker 6941e3e8)
Severity: 🔴 prod-real but latent (masked in production); silently kills the LLM judge in any manager-without-config context
Status: **NOT FIXED** (demonstration was report-only; fix recommended)

## Root cause

The LCA completion-gate marker-judge wrapper (`daemon/graph.py:~3419-3441`) and the would-be-deny-judge wrapper (`daemon/graph.py:~3703`) lazily load config inside a `try:` block:

```python
from ..config import load_config   # TWO dots — WRONG from daemon/graph.py
```

From `daemon/graph.py`, `..config` attempts to go above the `daemon` package → `ImportError: attempted relative import beyond top-level package`. Correct form: `from .config import load_config` (one dot).

## Why it never fired in production

The branch is `config = getattr(manager, "config", None) ... else: <import>`. `InstanceManager.__init__` always sets `self.config = config` (`daemon/manager.py:419`), so the buggy `else:` branch is dead code with the real manager. Any manager object lacking `.config` — test fixtures (`GraphTestManager`), future call sites — executes the import, and the broad `except Exception as marker_judge_exc:` swallows the ImportError and routes to fail-safe route (d): allow + hint (with pending) / deny (fail-closed), **the judge never runs**, and the only trace is `event=leader_completion_gate_marker_judge_error error_class=ImportError`.

## How it was detected (the transferable pattern)

1. The demonstration test stubbed `_invoke_judge_llm` with a call counter and asserted `judge_stub_calls >= 1`. First run: **0 calls** — the stub was never reached.
2. The canonical log carried `error_class=ImportError decision=fail_safe_marker_d` — the wrapper faulted BEFORE judge invocation.
3. Reproduction outside pytest (`uv run python -c` exec'ing the exact statement inside `daemon.graph`'s namespace) yielded the full traceback and proved the classification: prod-real (not a fixture artifact) — the one-dot form imports cleanly in the same harness.

**Lesson:** a stub-with-call-counter at the deepest seam catches silent wrapper faults that a verdict-only assertion cannot — the test initially "passed" (correct hint delivered via route (d)) while the designed route (b) never executed. Assert the ROUTE (`marker-path b` in log, `fail_safe_marker` absent), not just the outcome.

## Workaround (test-side, committed in 04873c62)

Attach `manager.config = load_config()` to the fixture manager after factory creation → `getattr(manager, "config", None)` is not None → buggy else-branch bypassed → true judge path executes (route (b) demonstrated with stub verdict + live verdict).

## Recommended fix (not applied)

1. `daemon/graph.py:3429`: `from ..config import load_config` → `from .config import load_config`
2. `daemon/graph.py:3703`: same one-char change
3. Regression test: construct a manager WITHOUT `.config`, run the marker path, assert `error_class=<none>` and the judge (stubbed) is actually invoked — pins the else-branch import forever.

## Blast radius if fixed

Tiny: the else-branch is currently dead in prod (masked); fixing it makes the lazy-import fallback actually work. No behavior change for real-manager paths.
