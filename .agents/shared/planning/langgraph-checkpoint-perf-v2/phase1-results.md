# Phase 1 — Results: PR1 Port (Instrumentation)

> Date: 2026-09-04 (UTC; work began 2026-09-03 ~23:51) | Working HEAD: `901d96e5` (branch `feature/langgraph-checkpoint-perf-v2`)
> Port method: manual re-apply (cherry-pick FORBIDDEN, not used). v1 branch touched READ-ONLY via `git show`/`git diff` only.
> DSN discipline: every DSN-resolving invocation carried BOTH `POSTGRES_URL=postgresql://ensemble:<pw>@localhost:5432/ensemble_cpv2_test` AND `POSTGRES_DB=ensemble_cpv2_test` (password from `POSTGRES_PASSWORD` env, composed inline, never written to any file). `ensemble_prod`/`ensemble_dev` never referenced.
> Nothing committed / staged / stashed. A commit worker follows.

## T1.1 — Diff analysis: DONE

Deliverable: `phase1-diff-analysis.md`. Key verifications:
- `git show 0db1a768 --stat` enumerated the full 13-file surface (13 files, +2125/−32) before any edit.
- Architect §1.2 claim VERIFIED: `git diff 58260f35 2f80d45b -- daemon/persistence.py daemon/services/maintenance.py` → 0 lines; re-confirmed against the live worktree (`git diff 58260f35 HEAD -- <both>` → 0). Both hot files were byte-identical between v1-base and v2-base → v1's hunks anchor mechanically.
- Drift scan `git diff 0db1a768 fc908945 -- <each file>`: 4 of 11 non-hot files drifted (checkpoint_perf.py 95 lines, GATE_SUITES 198, fixture-capture test 469, fixture JSON 59, perf-logging test 148); all others 0-drift.

## T1.2 — checkpoint_perf.py clean add: DONE (fc908945 byte target)

`fc908945:daemon/checkpoint_perf.py` → `daemon/checkpoint_perf.py`. **cmp: IDENTICAL.** The fc908945 version = 0db1a768's 129-line PR1 module + pure-appended `log_message_tap` (PR2) + `log_blob_prune` (PR4) — self-contained gated emit helpers, no new imports, no callers until Phases 2/4 (dead code, zero Phase-1 behavior). Chosen because the task explicitly mandates the fc908945 byte target; consequence: later phases will zero-diff this file for their helper appends.

## T1.3 — tools/lint/allowlist.txt: DONE

v2 had no existing file (verified before write → nothing clobbered). Ported v1's 498-byte / 10-line comment-header-only allowlist (**zero entries** — v1's "empty" state). **cmp vs 0db1a768: IDENTICAL** (≡ fc908945).

## T1.4 — persistence.py hot-file re-apply: DONE

Applied v1's exact per-file diff (`git show 0db1a768 -- daemon/persistence.py | git apply`) — manual re-apply executed against byte-identical anchors; **applied cleanly** (no conflict, as the architect correction predicted).
- H1 `import time` (stdlib block) ✓ · H2 `from daemon.checkpoint_perf import (checkpoint_perf_logs_enabled, log_messages_api, log_saver_op, time_saver_op)` ✓
- H3 `t0 = time.perf_counter()` at `get_instance_messages` head ✓ · `state = await time_saver_op("aget", instance_id, saver.aget(config))` ✓ · early-return `log_messages_api(…, 0, 0, 0)` ✓
- H3b alist walk kept INTACT, wrapped in try/finally with observed `alist_count` + `log_saver_op("alist", …)` (W2) ✓
- H4 gated `bytes_estimate` (S4/W3) + single final `[/Messages]` emit before the Phase-4 synthetic-injection section ✓

## T1.5 — maintenance.py hot-file re-apply: DONE

Same method (`git apply` of v1's per-file diff) — **applied cleanly**. `_prune_per_thread_checkpoints` (Operation D) now carries: docstring PR1 paragraph; function-local `import time` + `log_prune` import (v1 shape); `t0` + W7 counters initialized before the try; `log_prune("prune", 0, 0, 0, note="no excess threads")` on the no-excess path; `log_prune("prune-entry", …)` after the excess-pairs check; in-loop `observed_total_deleted` accumulation (partial counts on mid-loop failure); outer try/**finally** `log_prune("prune-exit", …)`. Existing `except Exception` semantics untouched (inner handler still swallows + logs). Operation E anchor (:448 call site; eventual PR4 site) untouched.

## T1.6 — GATE_SUITES.txt regen: DONE (fresh on v2, never copied)

- Structure copied from v1 (header/enumeration-record shape, `# File / N` comment table, non-manifest-companions note, spec/format notes, gate-concept scaffolding, exclusions trailer).
- Provenance recorded in-header: v2 HEAD `901d96e5`, date 2026-09-03 (UTC), method, DSN pinning, v2 addopts rationale.
- Per-file collect-only (`uv run pytest <file> -o addopts= --collect-only -q -p no:cacheprovider --no-header`, DSN-pinned, one subprocess per file): **23 rows = 22 v2-existing files (337 tests — matches phase0-pre-counts.md exactly) + `tests/unit/persistence/test_checkpoint_perf_logging.py` (19)**.
- **Aggregate cross-check (one subprocess over all 23 paths): `356 tests collected in 0.79s` = per-file sum 356. EXACT MATCH.**
- Manifest parser check (the ported dry-run gate's own `_parse_manifest` logic): 23 entries, 0 missing.
- dur(s) column deliberately omitted (collect-only regen; no fabricated timings).
- Only PR1 manifest row is the perf-logging test (per v1's own PR1-era 23-row manifest); fixture-capture / no-saver / gate-dry-run tests are non-manifest companions, matching v1's treatment.

## T1.7 — Port verification run: **19/19 GREEN**

```
POSTGRES_URL=…ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/unit/persistence/test_checkpoint_perf_logging.py -v -o addopts= -p no:cacheprovider --no-header
→ 19 passed in 0.67s (wall 1.35s — well under the 2-min cap)
```
Covers env-suppression, time_saver_op (incl. exception emission), observed-alist-count, **walk-exception emission** (exercises the persistence try/finally), **maintenance prune-entry/exit logging** (exercises the maintenance.py changes), fixture round-trip structural checks. No failures → no v1-vs-port diff investigation needed.

## T1.8 — Drift checks (scoped per task brief): ALL PASS, 0 port-caused deltas

| Check | Result | vs phase0 baseline |
|-------|--------|--------------------|
| G1 `grep -rn "settled" docs/job-task-system.md` | **17 lines** | Corpus doc says "14" — **the corpus doc's own verbatim output block lists 17 line hits (:409…:1188); `git grep -c "settled" 2f80d45b -- docs/job-task-system.md` = 17 and `git diff 2f80d45b HEAD -- docs/job-task-system.md` = 0 lines. The file is byte-unchanged since the baseline commit; "14" is an internal transcription error in phase0-grep-baseline.md, NOT a port delta.** Zero port-caused delta. |
| G2 `grep -n tap_node_return daemon/graph.py daemon/services/instance_messaging.py` | 0 matches, exit 1 | Exactly baseline (stays 0 until Phase 2) ✓ |
| G3 migration `20260*` tail | `20260819_000001_report_injections_deferred_marker.sql` | Exactly baseline (unchanged until Phase 2) ✓ |
| G4 `grep -rn atomic daemon/services/checkpoint_prune.py daemon/checkpoint_adapter.py` | exit 2, `checkpoint_prune.py: No such file or directory`; adapter-only `grep -n atomic` exit 1 (0 matches) | Exactly baseline ✓ |
| Facade guard `tests/unit/test_manager_enqueue_message_work_id_required.py` | **4 passed** | GREEN ✓ |
| Facade guard `tests/integration/test_job_driven_enqueue_work_id_facade.py` (DSN-pinned, `-o addopts=`) | **3 passed** | GREEN ✓ |
| WC-wake `tests/services/test_instance_messaging_queue_routing.py` | **15 passed, 1 failed** (`TestMessageRouteQueueIdForwarding::test_router_forwards_queue_id_to_enqueue_message_job` — router returns `INSTANCE_NOT_FOUND` where the test's mocked manager should yield 200) | Matches phase0-baseline's documented single pre-existing failure for this exact file (row 42 "registry/sentinel drift family"; 1F there, 1F here, 16 total both). Failure is at the router/fixture layer — zero overlap with the port's file set (my diff: persistence.py, maintenance.py + new files only). **Kill-switch state preserved: `ENSEMBLE_WC_WAKE_ENQUEUE` unset, no config key, readers at `daemon/routers/messages.py:172/:421` + `daemon/tools/instance.py:834/:3041` see flag-OFF default, matching phase0-state.md.** |

**Full-suite 0-delta comparison vs phase0-baseline.md: DEFERRED to the tester phase** (per task brief — full-suite/regression runs forbidden here). This deferral is recorded; the tester's net owns it.

### Supplemental targeted runs (canonical suites of the two edited hot files — both manifest rows)

| Suite | Result | Note |
|-------|--------|------|
| `tests/test_maintenance.py` | **69/69 passed** (1.08s) | Canonical suite for edited maintenance.py — runtime proof of zero prune-path behavior change |
| `tests/test_persistence.py` | **23/23 passed** (0.82s, DSN-pinned, `-o addopts=`) | Canonical suite for edited persistence.py — green solo (phase0's 15 documented FAILs were the batch-context SQLite-migration cascade family, row 18; solo at HEAD all pass — consistent with that family's documented context sensitivity) |
| `tests/integration/gate_suites/test_gate_suite_pause_resume.py` (ported) | **2 passed** (2.52s) | The enumeration gate itself validates the regenerated manifest end-to-end |

Not run (out of scoped T1.8, tester-phase surface): `test_messages_response_fixture_capture.py`, `test_no_saver_imports_in_routers.py` green-runs — their default mode is drift-check/AST-scan with `REGENERATE_FIXTURE=1` as the only write path; deliberately not executed here so the ported fixture stays byte-pristine.

## Zero-behavior-change proof (acceptance core)

```
$ git diff --stat -- daemon/persistence.py daemon/services/maintenance.py
 daemon/persistence.py          | 100 +++++-…
 daemon/services/maintenance.py |  88 +++++++—…
 2 files changed, 156 insertions(+), 32 deletions(-)

$ git diff --numstat -- <both>          $ git show 0db1a768 --numstat --format= -- <both>
 91  9  daemon/persistence.py           91  9  daemon/persistence.py      ← EXACT PARITY
 65 23  daemon/services/maintenance.py  65 23  daemon/services/maintenance.py ← EXACT PARITY
```

Deletion inventory (standard diff):
- **persistence.py — 9 deleted lines**: 8 are the alist-loop body re-indent under the new try/finally (**whitespace-only**, confirmed by `git diff --ignore-all-space` → exactly 1 deleted line); 1 is the mandated `aget` wrap (`- state = await saver.aget(config)` → `+ state = await time_saver_op("aget", instance_id, saver.aget(config))` — the original call preserved verbatim inside the wrapper).
- **maintenance.py — 23 deleted lines**: 18 whitespace-only (outer-try re-indent; `--ignore-all-space` → 5 deleted lines) + 5 that are **v1's own reviewed hunks** (the `total_deleted` → `observed_total_deleted` rename feeding both the in-loop W7 accumulation and the final `logger.info`, plus one comment-line extension). Semantics identical: same accumulation points, same reported value, same error swallowing.

Strongest fidelity evidence: **both hot files are byte-identical (`cmp`) to v1's post-PR1 state** (`git show 0db1a768:<path>`) — since v2-base ≡ v1-base and v1's exact diff was applied, the result reproduces the reviewed, round-2-APPROVED v1 shape with zero drift. `py_compile` OK on all 9 ported/edited .py files.

Honest note vs the task's "pure insertions" idealization: v1's own PR1 diff is not pure-insertion text (the aget wrap + the counter rename + try/finally re-indents). The port reproduces v1's hunks exactly rather than inventing a divergent "pure-insertion" reformulation of reviewed code; every `-` line is classified above (whitespace / v1's own reviewed transformation / the mandated wrap), and no v2-era content was deleted — the files entered this task byte-identical to v1-base, so every changed line is v1-base lineage, transformed exactly as v1 transformed it.

## Byte-equality table (T1.2 acceptance; `git show <src>:<path>` → /tmp → `cmp`; /tmp cleaned up after)

| File | Ported from | cmp vs source | cmp vs fc908945 |
|------|-------------|---------------|-----------------|
| `daemon/checkpoint_perf.py` | `fc908945` (task-mandated) | IDENTICAL (vs fc908945) | — |
| `daemon/persistence.py` | `0db1a768` applied diff | IDENTICAL (vs 0db1a768 post-PR1) | n/a (hot file; fc908945 carries later PR3 flips — not Phase-1 surface) |
| `daemon/services/maintenance.py` | `0db1a768` applied diff | IDENTICAL (vs 0db1a768 post-PR1) | n/a (hot file) |
| `tests/integration/gate_suites/GATE_SUITES.txt` | v1 structure + **fresh v2 regen** | by-design DIFFERS from both v1 commits (23 rows / 356 tests vs v1's 37/411) — that is the whole point of T1.6 | — |
| `tests/integration/gate_suites/__init__.py` | `fc908945` | IDENTICAL | IDENTICAL (0-drift file) |
| `tests/integration/gate_suites/test_gate_suite_pause_resume.py` | `fc908945` | IDENTICAL | IDENTICAL (0-drift file) |
| `tests/integration/test_messages_response_fixture_capture.py` | **`0db1a768`** | IDENTICAL | differs by documented PR3-era drift (see Deviation D1) |
| `tests/integration/test_no_saver_imports_in_routers.py` | `fc908945` | IDENTICAL | IDENTICAL (0-drift file) |
| `tests/unit/persistence/__init__.py` | `fc908945` | IDENTICAL | IDENTICAL (0-drift file) |
| `tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json` | **`0db1a768`** | IDENTICAL | differs by documented PR3-era drift (see Deviation D1) |
| `tests/unit/persistence/test_checkpoint_perf_logging.py` | **`0db1a768`** | IDENTICAL | differs by documented PR3-era drift (see Deviation D1) |
| `tools/lint/allowlist.txt` | `0db1a768` | IDENTICAL | IDENTICAL (0-drift file) |
| `.agents/tester/QUARANTINE.md` | **SKIPPED** | — | — (see below) |

## QUARANTINE.md decision: **SKIP** (per special handling; flagged for tester disposition)

v1's hunk appends 4 rows (3 files: `tests/integration/test_cold_resume_ttl.py` ×2, `tests/unit/test_question_deferred_pause_edge_cases.py` ×1, `tests/integration/test_pause_race_w7_jobitem_skip.py` ×1). Skipped because:
1. **Direct interaction**: v2's current ledger already covers `test_pause_race_w7_jobitem_skip ×1` inside its "M2-gate base-verified pre-existing additions (12 nodes)" family row (2026-09-03) with a DIFFERENT attribution (`MagicMock queue_type`) than v1's row (W7-marker guard expectation drift) — appending v1's row would create a conflicting duplicate registration.
2. v2's QUARANTINE.md is a restructured tester-owned ledger (50 lines); v1's rows cite v1-branch evidence (clean-tree stash @ `7a94162b`, 2026-08-25) that does not transfer, and whether the failures still reproduce at v2-base is unverified in this phase.
3. The instruction's own tiebreak: interaction or doubt → SKIP + document.
No byte of the file was touched (verified: absent from `git status`). **Tester disposition requested**: re-verify the 3 files against v2-base and register rows per the tester's own base-evidence protocol if still failing. The regenerated GATE_SUITES.txt carries a trailer pointing here.

## Deviations (with justification)

- **D1 — three "verbatim" files ported from `0db1a768` instead of the `fc908945` byte target** (`test_messages_response_fixture_capture.py`, fixture JSON, `test_checkpoint_perf_logging.py`): the fc908945 versions encode the post-C1 (PR3) contract (`saver.alist` never called / `alist_count=0` assertions; 6-variant fixture; synthetic-layer capture harness). At Phase 1 the alist walk is INTACT by design — those versions would fail T1.7 by construction and would falsify the fixture's documented "pre-C1 byte-shape contract for PR3" purpose. The task's own ground truth (`git show 0db1a768 --stat`) and line-count descriptors (657/128/510; 19 tests) match the 0db1a768 versions. The fc908945 growth of these files is the later phases' port surface.
- **D2 — QUARANTINE.md skipped** (see section above).
- **D3 — GATE_SUITES dur(s) column omitted**: collect-only regen; execution timings belong to tester-phase gate runs; none fabricated.
- **D4 — `checkpoint_perf.py` ported at the fc908945 byte target** (not 0db1a768's 129-line state): task-mandated; drift is pure self-contained append (dead code at Phase 1).
- **D5 — corpus erratum recorded, not edited**: phase0-grep-baseline.md Guard-1 "14 lines" mis-transcribes its own 17-line verbatim capture; docs are committed history and this phase commits nothing — recorded here + in the results for the tester/architect to correct.

## Final tree state (`git status --short`, uncommitted)

```
 M .agents/approver/active.md                                    ← pre-existing, untouched by this port
 M .agents/shared/planning/job-task-retrospective/decisions.md   ← pre-existing, untouched by this port
 M daemon/persistence.py                                          ← PR1 hunks (byte-identical to v1 post-PR1)
 M daemon/services/maintenance.py                                 ← PR1 hunks (byte-identical to v1 post-PR1)
?? .agents/shared/planning/defer-gate-fix/                        ← pre-existing, untouched by this port
?? .agents/shared/planning/langgraph-checkpoint-perf-v2/phase1-diff-analysis.md   ← T1.1 deliverable
?? .agents/shared/planning/langgraph-checkpoint-perf-v2/phase1-results.md         ← this file
?? daemon/checkpoint_perf.py                                      ← clean add
?? tests/integration/gate_suites/                                 ← manifest (regen) + __init__ + dry-run gate
?? tests/integration/test_messages_response_fixture_capture.py    ← verbatim (PR1-era)
?? tests/integration/test_no_saver_imports_in_routers.py          ← verbatim
?? tests/unit/persistence/                                        ← __init__ + fixture + 19-test suite
?? tools/                                                         ← lint/allowlist.txt (comment-header-only)
```
