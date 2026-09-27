# Whole-shard wedge class: attestation family × 30s thread-timeout (2026-09-27)

**Context:** maintenance-console whole-tree baseline (22,995 tests, 9 shards). Shard S5 (`tests/integration tests/api tests/opencode tests/performance`) wedged TWICE at different tests: first `test_attestation_bound_escalation.py::test_bound_plus_one_escalates_once_without_fourth_nudge`, then (after excluding that file) `test_attestation_attest_first_e2e.py`-family — same class: the test drives a leader loop with **real LLM judge calls** (`llm_judge_model=quick`, 10–14s per call) inside the default `timeout=30, timeout_method=thread` (pyproject). Thread-method cannot kill the wedged thread; pytest died **without printing any summary** → exit 1 with ZERO FAILED lines, i.e. a *silent wedge* that looks like "0 failures" if you trust the failures file.

**Resolution (TTQA):** exclude the whole `tests/integration/test_attestation*.py` family (34 files) from the shard; run the family **per-file** with `--override-ini='timeout=120'` → 34/34 pass/skip-identical at BOTH branch and base. The wedge is a **whole-shard contention phenomenon** (cross-file resource contention + real-LLM latency), not a per-file defect.

**Rules going forward:**
1. Exit 1 + no `short test summary info` line in the log = silent wedge, NOT a clean FAIL inventory. Never read an empty failures file as "green shard".
2. Whole-tree sweeps must give the attestation family its own pack (or per-file legs at `timeout=120`); do NOT let it ride inside a default-budget directory shard.
3. Real-LLM tests (`llm_judge_model=quick` lanes) are non-hermetic by construction under 30s budgets — classify as infrastructure, not product, when they wedge.
4. A/B delta with a wedged class: per-file parity at both commits (unchanged-files proof via empty `git diff` over the family) is a valid delta substitute for shard-level inventory.
