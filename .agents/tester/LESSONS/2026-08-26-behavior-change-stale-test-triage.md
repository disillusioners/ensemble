# Lesson: Intended behavior changes break frozen pre-branch tests — triage protocol + count-label decomposition

**Date**: 2026-08-26
**Context**: agent-instance-tools Phase 1 gate (`feature/agent-instance-tools` @ ab610121, base 6ca9541c)

## 1. Behavior-change fallout IS a "new failure" — and needs a 3-step triage, not panic

A deliberate contract change (RUNNING targets: enqueue → injection) broke 2 pre-branch tests that encode the old behavior. Full-tree sweeps + strict "any NEW failure is a blocker" rules will surface these every time a routing contract changes. The protocol that resolved it cleanly (all 3 steps dispatchable in parallel):

1. **Base-evidence worktree** (`git worktree add --detach /tmp/<name> <base>`): re-run failing files at base with the main `.venv` by absolute path. Classifies NEW vs PRE-EXISTING with byte-identical-file guarantee. Cheap (~5-9s for hundreds of tests). Worked flawlessly with venv reuse — no install needed.
2. **Branch-side deep-dive**: read the test's stub shape (what status does it report? what does it assert?) + the new routing table → verdict STALE-TEST vs REGRESSION. The discriminator: does the new code land in the branch the contract SAYS it should? If yes + test asserts the old branch → stale test. If the new code lands in the WRONG branch → regression.
3. **Report as blocker-with-disposition**: stale ≠ not-blocking (the test suite is red either way), but the fix is a test-only assertion update (~5 lines), not a production revert.

**Gotcha**: gate-rejection tests in the same class (deny paths) keep passing because rejection happens BEFORE routing — so class-wide runs underestimate fallout. Always check the class's happy-path tests specifically.

## 2. "N tests" claims from dev/review decompose across files

"92 cases" was actually 81 (test_instance_tools.py) + 11 (two guard suites) = 92; and 92 + 23 (pairing) = exactly the council's "115/115". Git history proved the file never held 92 (71→81→81). Before flagging a count mismatch as missing work, check (a) per-file sums across the claimed file set, (b) count-history `git log --follow` + collect at each commit, (c) whether the number came from a review aggregate. Zero tests were lost.

## 3. Full-tree sweeps surface pre-existing rot that scoped packs never see

~177 failures+errors across ~9.8k swept tests were ALL pre-existing — including a 45-test watchover `default_streaming` cascade that had never been quarantined because no committed pack covers those files. Two implications:
- A green PACKS.md ≠ green tree. Full-tree sweep gates (ad-hoc glob packs) are the only way to find these; run them at merge-gate time.
- Family-level QUARANTINE rows (not per-test) keep the ledger manageable when the count is 45+.
- `git worktree` base-verification makes "pre-existing" claims cheap and evidence-backed instead of hand-wavy.

## 4. Unguarded second lookup after a guarded first (split-cache race pattern)

`_resolve_instance_id` guards `get_instance` (async, ValueError → friendly), but a later UNGUARDED `get_instance_info` (sync, KeyError) at the CR-2 gate crashes on the cache-hit/store-miss race. Pattern: every manager lookup after the first existence check needs the same try/except if the contract promises friendly errors — the first guard does NOT cover later lookups. Caught only by a behavioral probe that deliberately desynchronized the two mocks (get_instance succeeds, get_instance_info raises). Unit mocks usually keep both consistent → suite stays green while the race exists.
