# LESSON — Stale-pin discovery class: "existing pins stay green" inventories must GREP for the retired contract, not trust the plan's named-pin list

- **Date**: 2026-09-08
- **Gate**: fix/kv-ambient-awareness final verification (RESULTS/2026-09-08-kv-ambient-final-gate.md)
- **Trigger**: Full-suite baseline attribution found 3 branch-introduced failures (pass @ 9eebf3ff base, deterministic fail @ b47085fd): `test_context_freshness::test_kv_written_mid_session_visible_next_call`, `test_context_hierarchy::test_child_human_messages_mode_uses_inherited_context_key` (both `'project' not in kinds ['shared_meta_kv']`), `test_context_in_graph::test_orchestrator_skips_persistent_rebuild_when_flag_true` (repo `get` called 1×, expected 0).

## What happened

The plan (phase1-plan.md test inventory) listed EXACTLY which "Existing pin" tests must stay green (`test_auto_load_skipped_when_project_already_injected`, `test_project_already_injected_skips_matcher`, `test_context_slot_resolves_fresh_flags`). The developer updated the pins named in the plan (`tests/unit/test_context_messages.py` flip + new files) — but THREE older integration tests outside the plan's inventory also pinned the retired contracts and were missed. All three carried literal comments pinning the old world ("KV lives there", "project_repo.get … NEVER called"), making adjudication fast.

## Root cause

A contract-moving branch retires behavior that may be pinned ANYWHERE in the corpus. The plan's pin inventory is a curated list, not an exhaustive one. No grep sweep for the retired contract's fingerprints was performed at plan time or implement time.

## Durable rule (test-architecture, applies to future contract-moving branches)

1. **Fingerprint-grep before merge**: for each retired contract, grep the test corpus for its fingerprints — distinctive assertion substrings AND comment phrases. Here: `"project" in kinds`, `"KV lives"`, `assert_not_called()` next to `_fetch_project_payload` mentions in docstrings. Two minutes of grep would have caught all three.
2. **Baseline-attribution gates must treat pass-at-base/fail-at-branch deltas as FIRST-CLASS adjudication items** — not automatically regressions, not automatically noise. The adjudication protocol that worked: (a) read the failing assertion + its comments; (b) trace the branch code path for the exact scenario; (c) cite the branch's own new-contract pins as the design authority; (d) 3× solo determinism at branch + 1× solo at base (xdist-artifact exclusion).
3. **Direction discipline**: base-only failures (fail at base, pass at branch) can NEVER be branch-introduced — classify as favorable/noise without blocking; branch-only failures need adjudication before verdict.
4. **Parallel fan-out contaminates concurrency-sensitive slices**: 16-way concurrent xdist caused 3 spurious `message_queue_redesign` SQLite commit-contention failures on the BASE side only. When running both sides of an attribution gate in parallel waves, expect concurrency-class noise on BOTH sides and prefer per-side comparison over absolute counts for that class.

## Follow-up shipped with this gate

- 3 stale pins identified with suggested replacements (RESULTS §Adjudication) — owner: branch dev, before/at merge.
