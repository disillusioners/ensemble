# LESSONS: LLM HA Failover v2 Campaign (secondary sites via shared facade)

Date: 2026-08-15 · Branch `feature/llm-failover-v2-sites` @ `8b135da7` (prod) / `7198bdd5` (test infra) · Verdict: SHIP

## What worked (reusable adversarial patterns)

### 1. Wire-level verification beats comparator unit tests (embedding guard)
The embedding guard's URL-normalization fix (trailing slash / host case equivalence) could pass a comparator unit test while being unwired at the call site. The adversarial suite verifies each guard matrix cell **on the wire**: MockTransport call-log shows whether the swap actually happened. Mutation-testing confirmed the suite catches the pre-fix comparator (8 tests fail) — a green comparator test alone would NOT have caught re-introduction.

### 2. AST structural pins for latency caps
`asyncio.wait_for(timeout=...)` presence verified via `ast.get_source_segment` + AST-walking, NOT regex (nested parens in `asyncio.wait_for(asyncio.to_thread(...), ...)` break `re`). Functional firing verified with monkeypatched short timeout + hanging LLM + `threading.Event` worker release — never literally waiting 30s.

### 3. Retry-sleep neutralization
The v2 base suite runs 92s due to tenacity `wait_exponential_jitter` retry ladders. An autouse fixture monkeypatching it to `wait_fixed(0)` cut the adversarial suite to ~1s. Apply this pattern to any future tenacity-based test pack.

### 4. Zero-drift testing at the SITE level, not facade level
Backup-unset zero-drift asserted per call site × per unset variant (`None` AND `""`) through the REAL site functions: (a) wire attempts ≤3, (b) zero requests off primary host, (c) graceful fallback reached, (d) no `[LLM-HA]` WARNING. The accepted delta (bounded ≤3 retry for previously-no-retry sites) is latency-only and council-adjudicated — do NOT flag it as a regression in future campaigns.

### 5. Lazy-import patch targets differ per site
`keyword_extraction` and `compaction` import `ThinkingChatOpenAI` INSIDE the function body — constructor-transport injection must patch `daemon.graph` for those sites (helper auto-detects which module to patch). Sites importing at module top patch their own module.

## Production-behavior facts pinned (differ from naive expectations)
1. `_normalize_endpoint_url` PRESERVES ports: `https://x:443/v1` ≠ `https://x/v1` → explicit default port on embedding override correctly DISABLES failover (conservative direction).
2. `skill_search._llm_select` PROPAGATES LLM exhaustion (does not return a default) — its graceful fallback lives one level up in `search()` → `_degraded_select`.
3. Nested `invoke_raw_with_failover` calls CLOBBER outer thread-local URL (single-depth slot, documented limitation, pinned by test).

## Quick fixes applied (both pre-existing latent breakage, NOT v2 bugs)
- `c75ebd14`: phase4 facade test asserted pre-hotfix `pause_instance_cascade` signature (missing `suspension_reason` kwarg from 2026-08-14 ask_questions fix).
- `6b41dd03`: two `InstanceManager.__new__`-built fixtures missing `_deferred_watchover_terminate` (added by Watchover commit `12378edb1`, 2026-08-06). **Class of breakage to audit**: minimal-manager fixtures bypassing `__init__` break whenever production adds attrs to `_cleanup_instance_state`.

## Baseline drift notes
- title_generation_trigger: documented baseline 21P/8F is STALE — the 8 failures were fixed pre-branch by `8c71b862` (test-side). Current baseline is 29/29. PACKS.md entry updated.
- report_repair (46) and skill_search_interval (22) baselines stale vs current file counts (61, 33) — suite growth, not failures.
- c2_core baseline: 38 pre-existing SQLite-migration failures confirmed 1:1 again; skip-count differs (14→0) across environments.

## Scope discipline
Diff was 10 files (brief said 12) — brief counted the 2 test-infra files written this campaign? No: actual prod diff = 10 files, 8 modified + 2 new. E2E mandatory gate verified NOT triggered (zero job/task/queue files in diff). Frontend N/A (zero files).
