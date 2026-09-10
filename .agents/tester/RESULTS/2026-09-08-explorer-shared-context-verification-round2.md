# Test Report: explorer shared-context branch — ROUND 2 (commits 3+4 delta, final merge gate)

Date: 2026-09-08
Branch: `feature/explorer-shared-context-injection` @ `d348ad4e` (tip) — delta verified: `b2e44d1e` (round-1 PASS) → `d097a8a2` (commit 3: W2 fix + upstreamed pack) → `d348ad4e` (commit 4: comment/doc/test-only). Base `fd582efd`.
Workers: 1=c87a096f (static-delta-analysis), 2=8d22b1dd (w2-regression-proof), 3=e8df30fc (groundtruth-d348ad4e)
Mode: independent verification; throwaway detached worktrees for runs (isolation proven via `daemon.__file__`), read-only git-object reads for statics; shared reviewer worktree untouched; no branch modification, no commits, no push, no restarts. Rev-parse guards recorded before every pytest invocation.

### Summary
- Items 1–4: **all PASS/CONFIRMED**; hygiene clean
- **Overall: READY-TO-MERGE** (3 non-blocking notes below)
- Failures attributed to the branch: **0** (tip fully green, 214P/1S/0F)

### Item 1 — Zero-logic-hunks claim (commit 4, `d097a8a2..d348ad4e`) — ✅ CONFIRMED
Topology: exactly 2 commits (`d097a8a2` fix(context), `d348ad4e` chore(context)). daemon/+docs/ scope: **18 changed lines across 4 files = 12 [COMMENT] + 4 [MARKDOWN] + 0 [CODE] + 0 [IMPORT-DELETION]** (numstat-verified, hunk-by-hunk):
- `daemon/persistence.py:900` — citation fix inside existing comment (`instance_lifecycle.py:3982-3998` → `3984-3994`)
- `daemon/services/instance_messaging.py:3651` — deleted 6-line pure-comment block (0 code)
- `daemon/tools/external_opencode.py:620` — stale comment rewritten (5 new `#` lines describing the system-orchestrator gate)
- `docs/context_injection_migration.md` — 2 single-line markdown rewrites (opt-in gate description)
Both import deletions in commit 4 are TEST-side (`SimpleNamespace` in the integration pack, `pytest` in parent_resolution test) — each verified **zero remaining references** at tip (behavior-neutral). W2 test rewrite (310 lines) = genuine test-side parametrize/restructure.
**Findings (non-blocking):**
- 🟢 Old class name survives in historical planning doc `.agents/shared/planning/context-auto-injection/phase3-plan.md:534,541` (spec block, not a live surface; same trap class as the LCA event-name case — grep sweeps for the old name will get false hits). Live surfaces (tests/docs/daemon): 0 stale refs; new name exactly at `tests/unit/tools/test_knowledge_tools.py:1007`.
- 🟢 Rewritten migration doc does NOT document the W2 `or None` normalization (doc-truth gap; minor).
- 🟢 Commit-3 `--stat` also touched `daemon/tools/knowledge_tools.py` (12±) — not itemized in the round-2 brief. Behavior is covered green at tip (120/120 knowledge_tools incl. all 5 renamed pins), and reviewer/tidier cycles closed on source hunks; recorded for completeness.

### Item 2 — W2 regression proof @ pre-W2 base `b2e44d1e` — ✅ CONFIRMED
Tip's `tests/unit/test_persistence_w2_parent_normalize.py` (1 test, 4 parametrize cases) copied into throwaway worktree at `b2e44d1e` (isolation proven):
- **Result: `1 failed, 3 passed`** — the legacy-`""` bug case fails with the exact symptom:
  `AssertionError: W2 normalization must produce parent_id=None for parent_id_value=''; got parent_id=''. assert '' is None`
  (empty-string parent_id reaching the resolver = mispartition shape). 3 companion cases pass (pin already-correct behavior), as expected.
- Fix-site evidence: BEFORE `b2e44d1e:daemon/persistence.py:899` = `parent_id = getattr(instance_meta, "parent_id", None)` (no normalization); AFTER `d348ad4e:daemon/persistence.py:905` = `… or None` + 6-line normalization comment.

### Item 3 — Ground truth @ `d348ad4e` — ✅ GREEN
Consolidated 6-file run, single `timeout 300` invocation, guard `HEAD/d348ad4e` recorded: **215 collected → 214 passed / 1 skipped / 0 failed / 0 errors in 6.13s**.

| File | Collected | Result |
|---|---|---|
| tests/unit/test_persistence_w2_parent_normalize.py | 4 | 4/4 P |
| tests/integration/test_explorer_shared_context_paths.py | 5 | 5/5 P |
| tests/unit/tools/test_knowledge_tools.py | 120 | 120/120 P |
| tests/services/test_instance_messaging_parent_resolution.py | 3 | 3/3 P |
| tests/unit/test_explorer_agent.py | 9 | 9/9 P |
| tests/unit/test_context_messages.py | 74 | 73 P + 1 S (pre-existing env skip, same as round 1) |

Rename: `TestExploreNoManualInjection` collects and runs 5/5 (all 5 test names recorded); old name 0 hits in tests/docs/daemon. Skip = deliberate env skip (`langgraph.graph.message unavailable`), identical to round 1.

### Item 4 — Upstreamed pack integrity @ tip — ✅ CONFIRMED (assertions STRONG, no weakening)
Diff vs tester's round-1 evidence (`/tmp/tester-evidence-explorer-ctx/test_explorer_shared_context_paths.py`, present):
1. L5 docstring: volatile `b2e44d1e` SHA pin removed (the "minus volatile sha" claim — exact match);
2. +10-line `ATTRIBUTION:` block (durable provenance, additive only);
3. dead `from types import SimpleNamespace` import removed (zero refs in both versions).
All 5 tests present under exact original names. Assertion-strength checklist:
- (i) tree-root partition assertion INTACT and STRONG (`L345-365`): `len(blocks) == 1` + `f"context_key: {root_id}" in block.content` + **two negatives** (`child_id` and `mid_id` context_keys asserted NOT in content);
- (ii) negative test asserts `blocks == []` across the turn with matchable content present;
- (iii) revive no-duplicate asserts `blocks == []` on the second turn;
- (iv) `len(blocks) == 1` exactness preserved in tests 1/3/5 — no `>= 1` weakening anywhere.
5/5 green at tip.

### Item 5 — Pre-existing hygiene — ✅ clean
Zero deterministic failures at tip in scope → base reproduction not triggered. Quarantine `tests/unit/tools/test_archive_lifecycle.py` excluded, untouched.

### ensure.md (scoped, same as round 1)
- Critical no-regressions-in-scope: PASS (214P/1S/0F). dev.sh `--timeout-graceful-shutdown 10`: PASS (`dev.sh:102`). Concurrency pack: out of delta blast radius (no lock-surface changes in commits 3–4).
- Important async-await callers: PASS — all 8 real call sites awaited (`routers/instances.py:375,525`; `routers/messages.py:757`; `tools/instance.py:2929`; `manager.py:8551`; `instance_messaging.py:1116,1146,1357`); rest = defs/docstrings/logs.
- Contradictions: none. Improvement notices: none.

### Gaps
None — all 3 workers reported; no incomplete nodes; all throwaway worktrees removed (`git worktree list` verified; shared reviewer worktree intact at `d348ad4e`).

### Non-blocking follow-ups (for the merge record)
1. 🟢 `phase3-plan.md:534,541` still prescribes the retired `TestExploreAutoInjection` name — annotate as historical or update (same false-grep-sweep trap class as the LCA event rename).
2. 🟢 `docs/context_injection_migration.md` lacks any mention of the `persistence.py` `or None` W2 normalization — one sentence would close the doc-truth gap.
3. 🟢 Commit-3 file set included `daemon/tools/knowledge_tools.py` (12±) beyond the brief's list — covered green at tip; note for the review record.

### Code Changes Summary
None (report-only arc; no repo/test-tree modifications; throwaway worktrees removed).

### Overall Status
- Item 1 zero-logic-hunks: ✅ CONFIRMED
- Item 2 W2 regression proof: ✅ CONFIRMED
- Item 3 ground truth @ d348ad4e: ✅ GREEN (214P/1S/0F)
- Item 4 pack integrity: ✅ CONFIRMED STRONG
- Item 5 hygiene: ✅ clean
- **Verdict: READY-TO-MERGE** — round-1 PASS at `b2e44d1e` + this delta verification at `d348ad4e` close the full arc (system injection on all paths, no manual attach, no double-injection, tree-root child partition, W2 legacy-`""` normalization).
