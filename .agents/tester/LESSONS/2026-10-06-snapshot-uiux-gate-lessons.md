# snapshot-uiux P6 gate — durable lessons (2026-10-06)

## 1. Hidden repo ini timeout guard masquerades as "structurally slow test"
`pyproject.toml:83-84` sets `timeout = 30` / `timeout_method = "thread"` (pytest-timeout). Thread-method fires a stack dump + `os._exit(1)` — exit **1**, no summary, no FAILED line. Three "killed-in-flight / structurally cannot fit 300s" readings for `test_attestation_incident_acceptance_lca.py::test_lca_b08f40fe_phase1…` were PHANTOMS: the test completes in **43s** once given a real budget (`--timeout=280` CLI override beats ini). Any "this test can't fit the pack" conclusion on this repo must first neutralize the ini guard explicitly.

## 2. `--tb=line --no-summary` emits ZERO tracebacks
pytest emits failure tracebacks ONLY in the summary section; `--no-summary` suppresses it and `--tb=line` only formats it. Under a `timeout` SIGTERM the summary is lost either way. To recover first-error lines for a killed batch: run each failing test in its OWN pytest invocation (each invocation flushes its own summary) — 6×~42s recovered 6 verbatim error lines after two batch attempts failed.

## 3. Integration dir needs ~60-test shards AND an LLM fence
`tests/integration/` avg ≈4.7s/test → a 300s pack holds ~60 tests (the 86-file half ≈ 40 min serial). Additionally, at least one test invokes a REAL OpenAI streaming call (`llm_failover` → `ssl.recv`) that hangs forever under restricted egress. House fence that works: `env -u OPENAI_API_KEY OPENAI_BASE_URL=http://127.0.0.1:9 OPENAI_API_BASE=http://127.0.0.1:9` — LLM calls fail FAST (connection-refused), no cap change needed. Fence was inert on the second half (identical 18-red set fenced vs unfenced) — apply it unconditionally to avoid the coin-flip.

## 4. `ScriptedChatModel exhausted` = completion-gate turn-budget drift, not prod bug
6 attestation tests (bound_escalation, idle_orphan ×2, incident_acceptance_lca phase1, revive_after_escalation, live_descendants ×2) fail identically: the leader completion gate's deny-loop + degenerate-retry path now consumes 2–5 more LLM turns than the scripts provide. All machine-proven pre-existing at base `ac399874`. Fix pattern: append `BaseMessage`s budgeting the nudge turns. Related: `test_attestation_live_descendants` fixture's `build_instance_llms` monkeypatch is BYPASSED on some paths (real `BaseChatOpenAI` reached despite the patch) — test-design gap, follow-up.

## 5. Full-tree collection on this repo LIES
`pytest --collect-only tests/` aborts after `tests/packs/` (module-level `sys.exit(0)` in `g7_unique_index_smoke_test.py:121`) — reported 4437 tests while `tests/unit/` alone holds 14708 (real tree ≈ 19k+). Never trust a flat `tests/` count; collect per-directory or `--ignore=tests/packs`.

## 6. Attribution protocol that scaled (216 reds → verdict, zero guessing)
Red-at-HEAD → re-run the exact node IDs (whole FILES for dense identical-signature families) at the branch base in a detached, uv-synced, `daemon.__file__`-verified worktree → red-at-base = pre-existing. Crucial control: keep the ENV IDENTICAL on both sides — we had 2 green-at-base flags that a HEAD-side re-run under the base leg's fence resolved as env-shaped (3/3 PASS), not branch-caused. One fence cross-check saved a false gate-block.
