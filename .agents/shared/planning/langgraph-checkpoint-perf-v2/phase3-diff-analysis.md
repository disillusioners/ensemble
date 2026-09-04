# Phase 3 — T3.1 Diff Analysis: v1 PR3 (5d928d51 + dbfbf812 + c5dae6a5) → v2

> Date: 2026-09-04 (UTC) | Working HEAD: `2ded9c9a` (branch `feature/langgraph-checkpoint-perf-v2`)
> v1 branch (READ-ONLY): `feature/langgraph-checkpoint-perf` @ `c37c870c` (port boundary `fc908945`)
> Method: read-only diff analysis only; cherry-picks start in T3.2.

## 1. Per-commit surface (from `git show --stat`)

| v1 SHA | Subject | Files | Strategy |
|--------|---------|-------|----------|
| `5d928d51` | test(perf): PR3 pre-flip — freeze synthetic layer + empty-path contract | 3 MODIFIED, 0 created | Low conflict — fixture-capture test +231, fixture JSON +40, perf-logging test +7 |
| `dbfbf812` | feat(perf): PR3 — C1 read flip, aget-only + metadata timestamps | 6 (1 HOT + 5 created) | HOT on `daemon/persistence.py`; CLEAN adds for 5 new test files |
| `c5dae6a5` | fix(perf): PR3 review folds — guard warning + caplog pin, doc reword | 3 MODIFIED | Hot fold on `daemon/persistence.py` (warning else-branch); minor fold on `message_tap.py` docstring; +23 lines on no-alist test |

**Total:** 11 files touched (1 daemon HOT + 1 daemon doc fold + 9 test files); 0 new daemon source files; 5 new test files; 4 test files modified.

## 2. CRITICAL VERIFICATION (architect §1.2 anchor)

`git diff 58260f35..2f80d45b -- daemon/persistence.py` returns **EMPTY** (0 lines).
Confirmed independently: `git log 58260f35..2f80d45b -- daemon/persistence.py | wc -l` = **0**.

This matches the architect §1.2 corrected claim: v1 and v2 are byte-identical on `daemon/persistence.py` between v1-base (`58260f35`) and v2-base (`2f80d45b`). The v1 hunks anchor on byte-identical text — cherry-pick will replay v1's diff verbatim.

(PR1 added ~24 lines of instrumentation INSIDE/ADJACENT to the alist block, but the file is byte-identical between v1-PR1-merge-state and v2-PR1-merge-state because both branches received the SAME PR1 commit. PR1 is already on both branches at the relevant parents.)

## 3. Per-file hunk shapes

### 3.1 `5d928d51` (pre-flip freeze — fixture extends; daemon code untouched)

#### 3.1.1 `tests/integration/test_messages_response_fixture_capture.py` (+231/−26)

| # | v1 hunk (`@@ old`) | Shape |
|---|--------------------|-------|
| H1 | `@@ -17,7` | Doc reword (4 variants → "ALL variants") |
| H2 | `@@ -30,17` | Provenance block: rewrites synthetic-layer paragraph; documents 5-6 variants + arming |
| H3 | `@@ -52,7` | Schema doc reword (4 → 6 variants) |
| H4 | `@@ -81,6` | Import: `from unittest.mock import MagicMock, patch` |
| H5 | `@@ -259,11` | `_capture_variant` signature changes (messages_to_inject Optional; +manager, +synthetic_system_prompt kwargs) |
| H6 | `@@ -284+` | Two new variants added (5-EMPTY + 6-SYNTHETIC_SYSTEM) |

v2 anchor: same file exists at v2-tip (PR1 already added it; v1's 5d928d51 extends). v2 line range can be found via `grep -n "_capture_variant\|variants = " tests/integration/test_messages_response_fixture_capture.py`. Since the file is a clean PR1 add at v2, v1's pre-flip delta should apply cleanly.

#### 3.1.2 `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` (+40 lines)

Adds 2 new variants to the frozen fixture JSON. dbfbf812 also touches this file (potentially adding more, but verification pending T3.4).

#### 3.1.3 `tests/unit/persistence/test_checkpoint_perf_logging.py` (+7 lines)

Minor: env-suppression test or assertion updates.

### 3.2 `dbfbf812` (C1 read flip — HOT on persistence.py + 5 clean adds)

#### 3.2.1 `daemon/persistence.py` (HOT — replay v1 hunks verbatim)

| # | v1 hunk (`@@ old`) | Shape | v2 anchor (identical text) |
|---|--------------------|-------|----------------------------|
| H1 | `@@ -31,7` | DELETE `log_saver_op` from checkpoint_perf imports | v2 `:35-39` (same `from daemon.checkpoint_perf import (...)` block) |
| H2 | `@@ -272,9` | REPLACE docstring alist mention (now GONE post-C1) | v2 `:316-325` (same docstring) |
| H3 | `@@ -285,6` | ADD Phase-1-C1 paragraph (message_metadata_repo lookup) | v2 `:329-345` (same docstring end) |
| H4 | `@@ -305,9` | DELETE `from typing import cast` + `from langgraph.checkpoint.memory import CheckpointTuple` + blank | v2 `:355-358` (same import block) |
| H5 | `@@ -316,10` | REPLACE alist comment (now gone) | v2 `:367-377` |
| H6 | `@@ -334,9` | REPLACE early-return alist_count comment (0 by absence now permanent) | v2 `:388-394` |
| H7 | `@@ -346,50` | **DELETE alist walk block (35 lines) + checkpoints_data loop + msg_timestamps build loop (15 lines) → REPLACE with C1 side-table enrichment block (51 lines)** | v2 `:403-451` (v1 lines 326-373 map verbatim to v2 lines 350-397) |
| H8 | `@@ -421,12` | REPLACE log line alist_count comment | v2 `:530-540` |
| H9 | `@@ -435,6` | ADD Phase-1-C1 grep-friendly invariant comment | v2 `:545-555` |
| H10 | `@@ -455,7` | REPLACE `alist_count` literal → `0` in log line | v2 `:571` |

**Critical anchor — DELETE alist walk block:**
- v2 lines **350-396** (47 lines): `checkpoints_data` declaration + alist walk try/finally + `checkpoints_data.reverse()` + `msg_timestamps` build loop.
- v1 lines **326-373** (48 lines): same content (PR1 added the perf_counter + try/finally wrapping).
- DELETE: v2 lines 350-396 (47 lines)
- ADD: v1-equivalent new block (51 lines: side-table enrichment block)

**v2 alist walk (lines 350-396) — for the EDIT pass:**
```
350: checkpoints_data: list[tuple[str | None, list[Any]]] = []
351: 
352-365: PR1 comments + counter + timing init (14 lines)
366:        async for checkpoint_tuple in saver.alist(config, limit=1000):
367:            ...
368-373: body of alist loop (6 lines)
374-379: finally block (log_saver_op call)
380:     # Reverse to get oldest-to-newest order
381:     checkpoints_data.reverse()
382: 
383:     # Track when each message first appeared
384:     msg_timestamps: dict[str, str] = {}
385:     for ts, checkpoint_messages in checkpoints_data:
386-390: body of msg_timestamps build
391-396: ...
```

#### 3.2.2 CLEAN ADD test files

| File | v1 source | Strategy |
|------|-----------|----------|
| `tests/unit/persistence/test_get_instance_messages_no_alist.py` | dbfbf812 (NOT 5d928d51) | Armed-absence alist proof; PRIMARY C1 boundary |
| `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` | dbfbf812 | Frozen-fixture byte-shape contract (poison-pill alist) |
| `tests/integration/test_message_metadata_lifecycle_wiring.py` | dbfbf812 | **Already ported in Phase 2** (C7.1: `dc39ae6d`); cherry-pick will be a no-op for this file (3-way merge should skip it) |
| `tests/integration/test_messages_response_fixture_capture.py` | dbfbf812 (modifies) | Modified by BOTH 5d928d51 (variant adds) and dbfbf812 (capture suite split: shape byte-for-byte, observed_alist_count excluded, loud-regen baseline guard, post-C1 disappearance gate) |
| `tests/unit/persistence/test_checkpoint_perf_logging.py` | dbfbf812 (modifies) | Modifies + drift by c5dae6a5 |

**Attribution fix (already flagged in plan T3.4):**
- `tests/unit/persistence/test_get_instance_messages_no_alist.py` arrives via `dbfbf812` (T3.3), NOT `5d928d51`.
- `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` arrives via `dbfbf812` (T3.3), NOT `5d928d51`.
- The fixture JSON `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` is touched by BOTH `5d928d51` (T3.2, +40 lines for variants 5-6) AND `dbfbf812` (T3.3, potentially more — to verify at T3.4).

### 3.3 `c5dae6a5` (review folds — 3 files, 35+/6−)

| File | Hunk | Shape |
|------|------|-------|
| `daemon/persistence.py` | `@@ -403,6` | ADD `else:` branch to the `if msgs_repo is not None:` block (lines right after metadata = {} reset on exception) — emits a WARNING identifying instance + state.ts fallback + cause. Fires only on None/missing branch. |
| `daemon/services/message_tap.py` | `@@ -84,9` | Doc reword: "joins message_metadata to the checkpoint walk" → "joins at the aget-only serialization loop". |
| `tests/unit/persistence/test_get_instance_messages_no_alist.py` | `@@ -158,6` | Caplog assertion: converse test gets `assert not [...message_metadata_repo missing/None...]`; also `test_manager_without_repo_attribute_degrades` gets caplog param + assertion `len(warns) == 1` + "state.ts" + "thr-attr" checks. |

**Catch is `except Exception:` (C-14)** — VERIFIED in dbfbf812 (the `try / except Exception as exc:` block); the c5dae6a5 fold sits OUTSIDE the try (it's the `else:` branch). No `except BaseException:` anywhere.

## 4. T3.9 — `tests/integration/test_no_saver_imports_in_routers.py` (architect §5 guardrail row 1 + §8.3)

This file is NOT in any of the 3 PR3 commits. It must be clean-added from `fc908945` (the v1 binding-gate §33 import-level hard-fail test).

Verification:
- v1 already has the file (created in PR1 by `0db1a768`; preserved through to `fc908945`).
- v2 currently does NOT have the file (verified via `ls tests/integration/test_no_saver_imports_in_routers.py` → not found).
- Source: `git show fc908945:tests/integration/test_no_saver_imports_in_routers.py` → 281 lines, 6 tests (AST import scan over `daemon/routers/**`; allowlist still EMPTY).

**Phase 5 T5.13 EXTENDS this file with AST call-func scan (`.alist(`).** Phase 3 is the CLEAN-ADD that establishes the file on v2.

## 5. Conflict risk summary

| File | Conflict | Reason |
|------|----------|--------|
| `daemon/persistence.py` | **ZERO** | byte-identical between v1-base and v2-base; v1 hunks replay verbatim |
| `daemon/services/message_tap.py` | ZERO | only a doc reword; no semantic change |
| `tests/integration/test_messages_response_fixture_capture.py` | LOW | 5d928d51 extends docstring + adds variants 5-6; dbfbf812 may further split the capture suite (TBD at T3.4); the file exists on v2 from Phase 1 PR1 |
| `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` | LOW | modified by both 5d928d51 (T3.2) and dbfbf812 (T3.3); cherry-pick of dbfbf812 may auto-resolve by accepting the union (T3.4 verifies) |
| `tests/unit/persistence/test_checkpoint_perf_logging.py` | LOW | modified by 5d928d51 + dbfbf812 + c5dae6a5 (all additive) |
| `tests/unit/persistence/test_get_instance_messages_no_alist.py` | n/a | clean add |
| `tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py` | n/a | clean add |
| `tests/integration/test_message_metadata_lifecycle_wiring.py` | n/a | already on v2 (C7.1); dbfbf812 will auto-resolve (3-way merge: same content both sides) |
| `tests/integration/test_no_saver_imports_in_routers.py` | n/a | clean add (T3.9) |

## 6. Rollback plan

Per-phase3-plan.md §"Rollback Note": regen last, `c5dae6a5` third, `dbfbf812` second, `5d928d51` first.

`git revert` per Phase 3 commit. The commit set is: `<cherry-pick-5d928d51>` → `<cherry-pick-dbfbf812>` → `<cherry-pick-c5dae6a5>` → `<gate_suites-regen>` → `<no-saver-imports-test>`.

## 7. STOP-gate checks (pre-T3.2)

- [x] `daemon/persistence.py` byte-identical between v1-base and v2-base (verified)
- [x] 3-way merge expected to succeed (no semantic conflict)
- [x] DSN discipline will be applied to every DSN-resolving test invocation
- [x] v1 branch remains READ-ONLY (only `git show` / `git diff` used)
- [x] No `git add -A` / `.` / `-a` (explicit paths only)

## 8. Open verifications (deferred to T3.3 / T3.4)

- The exact line range for v2's alist walk: **v2 lines 350-396** (confirmed via `grep`).
- Whether dbfbf812's frozen-fixture test asserts byte-equal response shape (T3.4 will verify via `git show dbfbf812:tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py`).
- Whether the fixture JSON's v2 form (after T3.2) is a strict subset of v1's `fc908945` form (T3.4 verifies via `cmp`).

## 9. T3.9 verification (executed in T3.9)

- `tests/integration/test_no_saver_imports_in_routers.py` IS already on v2 from PR1 commit `87ad1018` (Phase 1 C4 instrumentation); **byte-identical to v1 `fc908945`**.
- 6/6 GREEN (allowlist EMPTY).
- **No new commit needed** — file is clean at HEAD.
- T3.9 is therefore a **NO-OP** (verified, not ported).