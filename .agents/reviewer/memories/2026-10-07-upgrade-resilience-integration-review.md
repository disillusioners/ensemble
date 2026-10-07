# 2026-10-07 — Integration review: feature/upgrade-executor-resilience @ dc17a9142

**Verdict: APPROVE** — Deep-Review council (models `agentic` + `coding`, both APPROVE). 0 critical, 0 merge-blocking. 1 🟡 non-blocking warning surfaced.

## Outcome — 7/7 cross-lane seams PASS at tip dc17a9142 (15 commits on latest@2753ee78d, clean worktree)
1. Terminal-event taxonomy coherence — PASS (9-member terminal set incl. refusal/executor_exit/boot_sweep_commit_and_continue; commits 57ca613bf + f8c9958a7)
2. Option D end-to-end raise path — PASS; narrow `except` at `manager.py:4598` BEFORE broad `:4625+`; re-land confirmed; strip/re-land trio `27b48af20 → d1a771253 → dc17a9142` preserved attribution — **merge must NOT squash**
3. Authority invariants — PASS (unit carries survivability not authority; require_live_guard lib.sh:327-341; env-allowlist pre/post-gate semantics unchanged)
4. Bounded-wait × recovery interplay — PASS (commit 83f0b53a5; per-site recovery, no half-mutated-state-with-daemon-down site found)
5. Boot-sweep gate conjunction — PASS (launcher.sh:925-1042; commit d9d07bf0c)
6. Stage payload × staleness triage — PASS (exit-78 precondition not bypassable via --allow-stale-stage; commit 55dd7f78c default-flip)
7. Commit hygiene — PASS (spot-checked last 4)

## 🟡 Non-blocking finding (council disagreement — severity-axis placement, not correctness)
`STOP_SCRIPT_BUDGET_S` fixed default 120s (`lib.sh:94`) is NOT derived from the child's resolved `WAIT_S` (default 70s, override up to 600s, `stop-ensemble.sh:110-303`). Operator `WAIT_S>120` → parent SIGKILLs stop child mid-graceful-wait → premature rc=0 fall-through → flip may proceed against a still-live daemon. Blast radius bounded by downstream gates/rollback/boot-sweep.
**Fix shape:** derive budget = resolved_WAIT_S + margin, or clamp/validate explicit WAIT_S against budget at the stop site. Dev-lane follow-up, not merge-blocking.

## Process notes
- Deep-Review triggers fired: security (authority/env-forwarding), concurrency/state (bounded waits, boot sweep), cross-cutting (shell↔daemon taxonomy), architecture (Option D executor).
- Councilor-level detail: governor instance e5bd43d7-c7cb-4a57-b9cd-43efa41f9b64.
- Runtime behavior was out of scope — a tester instance ran concurrently; this APPROVE is code-level only.
