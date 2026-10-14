# PR4 Blob-Prune Race-Fold — Formal Re-Review (v2 Port) — T5.7 / FR-8

> **Verdict stamp: ✅ APPROVED — LOOP CLOSED**
> Per §5 semantics: every original finding RESOLVED + binding gate 9/9 GREEN on the reviewer's PERSONAL re-run + ZERO new findings from the port-only delta scan. All three conjuncts TRUE.

This file is the T5.7 (FR-8) formal re-review artifact. It closes the v1 process gap: v1's PR4 external
review returned NEEDS_CHANGES; the fix folds landed in v1 `7a7998fe`, but "targeted re-review SATISFIED"
was recorded only in a commit message — no formal approval artifact existed. This document is that missing
re-review, executed against the **v2 PORT** of PR4. Authored by the dispatched reviewer instance — NOT the
implementer (AC-8.4). Left untracked for the developer to commit.

---

## Section 1 — Header

| Field | Value |
|---|---|
| Date | 2026-09-04 (UTC), 06:51–07:05Z review window |
| Branch / reviewed SHA | `feature/langgraph-checkpoint-perf-v2` @ `e52d845e` (rev-parse bracketed around the gate run; no drift) |
| v1 cherry-pick sources (spec §1) | `f89ccacc` (feat: reference-aware checkpoint_blobs prune) · `7a7998fe` (critical race folds: SERIALIZABLE wrap + retraction + race tests) |
| v2 port commits (−x provenance verified in commit bodies) | `f8d59c01` ← `f89ccacc7bedd517895357128fde6270ff0f7e23` · `a1ae0f91` ← `7a7998fe52a189af0b462e3ec2dae68e4bfa4100` |
| v2 supporting commits | `1569c82a` (gate manifest regen) · `84894b74` (docs/runbook + planning docs) · `d5f3a2b0` (post-review corrections) |
| Reviewer-instance | `reviewer[v2]` controller, dispatched under tree-root `347fa33b-b135-4f20-b1f4-f61aad722924` (LangGraph Checkpoint Performance Improvements). Deep-Review council governor spawned BY this reviewer: `06ac38f1-3156-4d36-89e1-6e6ec5c33b8e` (instance `t57-pr4-rereview-council`) — authorship evidence chain for AC-8.4 |
| Verdict stamp | ✅ APPROVED — 0 🔴 / 0 🟡 / 0 🟢 new findings from the port |
| v1 NEEDS_CHANGES doc (SHA-ref) | `c37c870c:.agents/reviewer/memories/2026-08-26-pr4-blob-prune-deep-review-needs-changes.md` (read in full by this reviewer) |
| Method | 🔴 Deep-Review (multiple trigger categories: data-integrity destructive prune, complex concurrency/race folds, cross-cutting adapter+maintenance surfaces). Static adversarial verification by 2-model council (`agentic` + `coding`, `code-review` skill per councilor, full convergence, zero disagreements); binding gate executed PERSONALLY by the reviewer (spec §4). |

---

## Section 2 — Per-Finding Resolution Table

Original findings enumerated from the v1 NEEDS_CHANGES doc (1 🔴 + 2 🟡).

| # | Original finding (v1) | Required fold | v1 fold evidence (`7a7998fe`) | v2 post-port evidence (this re-review) | Status |
|---|---|---|---|---|---|
| F1 🔴 | **aput non-atomicity race, undisclosed + falsely claimed closed.** AsyncPostgresSaver.aput default PG14+ pipeline path commits blob upsert and checkpoint upsert as two separate implicit transactions (µs gap) — constructible silent data-loss window under armed destructive prune | (1) retract the "atomic" docstring claim, cite `aio.py:82, 280-304, 393-399`; (2) runbook §7 intra-process race sentence + backup-covers-recovery note; (3) close structurally (PREFERRED: SERIALIZABLE-wrapped DELETE + serialization-retry) OR deterministic race demonstrator | (1) retraction in gate module docstring `checkpoint_prune_real_saver.py:44-63` + adapter rationale `:688-693`; (2) runbook §7 `checkpoint-blob-prune-restore.md:155-191`; (3) SERIALIZABLE wrap + 40001 retry in `delete_blobs_anti_join` | **All three folds present in port.** Council re-verified every `aio.py` citation against the INSTALLED langgraph-checkpoint-postgres 3.1.0 source (dist-info confirmed; `aio.py:78-84`, `280-304`, `390-401`) — citations accurate. Item-4 sweep: zero surviving atomicity claims anywhere on the touched surface. Reviewer's PERSONAL gate run: `TestRealSaverSerializableRetry` PASSED (real SSI abort, deterministic 40001 from a partner SERIALIZABLE txn, retry completes on fresh snapshot, `sqlstate=40001` asserted) + `HONEST LIMIT` paragraph (`adapter:716-727`) explicitly forbids restating the wrap as eliminating the race | **RESOLVED** |
| F2 🟡 | **One-directional concurrency coverage** — only asserted NEW blob survives; pre-existing-referenced-blob survival across interleaved multi-turn aputs + destructive prune (byte-equality) untested | Add bidirectional coverage | `TestRealSaverRaceWindow` added (`checkpoint_prune_real_saver.py:666-780`) | Present and PASSED on the reviewer's personal 9/9 run: 8 seeded real graph turns → retention prune → 10 more real multi-turn aputs via `asyncio.gather` (`:738-742`) overlapping an armed destructive prune; asserts exactly-orphans-die, pre-existing referenced blobs survive **md5 byte-equal** (`:750-756`), full-history `aget` reconstruction incl. prefix equality (`:772-780`). Council assessed interleave as genuine (cross-connection: psycopg autocommit-pipeline traffic vs asyncpg prune pool — every aput really traverses the two-commit µs gap), **not sequential theater** | **RESOLVED** |
| F3 🟡 | **Harness topology** — single psycopg conn vs prod reality of asyncpg-pool-vs-psycopg | Add separate-pool fixture variant | `race_stack` fixture (`tests/helpers/checkpoint_prune_pg.py:178-241`) | Present in port (patch-identical to v1): separate asyncpg prune pool + saver psycopg connection; exercised by RaceWindow and SerializableRetry on the reviewer's personal run | **RESOLVED** |

---

## Section 3 — 9-Item FR-8 Verification Matrix (AC-8.2)

| # | Item | Evidence (provenance marked) | Verdict |
|---|---|---|---|
| 1 | Binding gate 9/9 on real PG | [REVIEWER-PERSONAL] §4 below: 9 passed, 0 failed, 0 skipped, 4.04s, PG 14.22, `ensemble_cpv2_test`, @ `e52d845e` | ✅ |
| 2 | 40001 retry GREEN | [REVIEWER-PERSONAL] `TestRealSaverSerializableRetry::test_real_40001_aborts_delete_then_retry_completes` PASSED; [COUNCIL] real deterministic SSI abort (partner SERIALIZABLE txn supplies second rw-edge via `UPDATE checkpoints`; PG itself aborts the parked DELETE), retry WARNING `sqlstate=40001` asserted `:906-912`, retry completes on fresh snapshot `:894-902`; no mocks/error-injection | ✅ |
| 3 | Bidirectional race byte-equal | [REVIEWER-PERSONAL] `TestRealSaverRaceWindow::test_preexisting_referenced_blobs_survive_traffic_and_prune_byte_equal` PASSED + `TestRealSaverConcurrentAput` PASSED; [COUNCIL] genuine cross-connection interleave verified (see §2 F2) | ✅ |
| 4 | aput retraction note citing `aio.py:82, 280-304, 393-399` | [COUNCIL] `daemon/checkpoint_adapter.py:688-693` (+ `HONEST LIMIT` `:716-727`); module-level retraction `checkpoint_prune_real_saver.py:44-63`; all three citations independently verified against installed 3.1.0 source (`aio.py:78-84`, `280-304`, `390-401`) | ✅ |
| 5 | `_DELETE_RETRIES` in constants | [COUNCIL] `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3` at `daemon/constants.py:92`; consumed at `checkpoint_adapter.py:728`; bounded retry with 50ms·2ⁿ backoff `:779-795`; exhaustion → ERROR + skip + `(0,0)`, never raises `:763-778` | ✅ |
| 6 | Structural-unreachability AST gate GREEN | [COUNCIL, re-run] Gate located at `tests/unit/services/test_maintenance_prune_direct_anti_join.py:112-194` (`TestStructuralGate::test_delete_call_is_structurally_gated_by_destructive_flag` — AST-parse of `checkpoint_prune.py`, proves the `if not destructive … continue` guard textually dominates the single `delete_blobs_anti_join` call; belt: exactly-one-call-site `:196-204`, runtime sentinels `:205-240`, 8-combo flag matrix `:81-103`). Council re-ran: structural gate 4/4 GREEN (0.96s); full file 24/24 GREEN @ `e52d845e` (non-DB, PG env pinned) | ✅ |
| 7 | `find_all_thread_ns_pairs` invoked | [COUNCIL] Signatures on ABC + both impls `:139/:351/:583`; invoked at the real production call site `daemon/services/checkpoint_prune.py:138` | ✅ |
| 8 | The 3 anti-join method signatures present | [COUNCIL] `count_refs_for_blob_thread` `:158/:371/:604` · `count_blobs_anti_join` `:176/:388/:638` · `delete_blobs_anti_join` `:193/:398/:662` | ✅ |
| 9 | Runbook §7 disclosure present | [COUNCIL] `docs/runbooks/checkpoint-blob-prune-restore.md:155-191`: intra-process framing explicit ("the window exists even with exactly one daemon process", `:155-161`); µs-window + idle-gate-is-a-precondition caveat `:163-176`; backup-covers-recovery (`:174-176`, `:188-191`) | ✅ |

Supplementary (not in the 9, recorded for completeness): anti-join predicate independently re-derived by both
councilors against the installed 3.1.0 `SELECT_SQL` reader keyset — **exact complement, zero cast drift**
(version keys TEXT `f"{next_v:032}.{next_h:016}"`, ns correlated both sides, thread pinned, `jsonb_typeof`
guard → 0 refs → ZERO_REFS fail-safe trips pre-arming); safety ladder defaults `constants.py:84-85`
(`DRY_RUN=True` / `DESTRUCTIVE=False`), call-time reads via `blob_prune_destructive_enabled()`
(`prune.py:80-84`, per cycle `:132`), ZERO_REFS ERROR before the arm split `prune.py:155-179` (guards both
arms even with `DESTRUCTIVE=1`), SQLite no-op stubs `(0,0)` + WARNING (`adapter:388-411`).

---

## Section 4 — Re-run Evidence (REVIEWER-PERSONAL — not a citation of the implementer's run)

- **Timestamp:** 2026-09-04T06:57:06Z → 2026-09-04T06:57:12Z (UTC)
- **Disposable-DB identity:** `postgresql://ensemble@localhost:5432/ensemble_cpv2_test` — PostgreSQL 14.22 (Homebrew, aarch64) — satisfies PIN-PARITY ≥14.22
- **DSN discipline:** BOTH `POSTGRES_URL` and `POSTGRES_DB` pinned on the command. Session env carries `POSTGRES_DB=ensemble_prod`; it was explicitly overridden on every command. `ensemble_prod` / `ensemble_dev` never referenced by any command in this review.
- **Rev-parse bracket:** `feature/langgraph-checkpoint-perf-v2` / `e52d845e` BEFORE and AFTER the run — no mid-run drift.
- **Pre-run DB state:** `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` = 0 rows (`checkpoint_migrations` = 10, harness bookkeeping); no foreign data — no STOP condition.

Command (verbatim):

```
POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test POSTGRES_DB=ensemble_cpv2_test \
  uv run pytest tests/integration/checkpoint_prune_real_saver.py -v
```

Output (verbatim, abridged only in the session-header lines):

```
collected 9 items

tests/integration/checkpoint_prune_real_saver.py::TestRealSaverWritePruneResume::test_real_saver_write_retention_prune_blob_prune_resume PASSED [ 11%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverWritePruneResume::test_real_saver_kill_safe_restart_reconstruction PASSED [ 22%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverDeltaSnapshotChain::test_delta_chain_snapshot_blob_survives_and_orphan_snapshot_dies PASSED [ 33%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverFailSafe::test_real_saver_zero_refs_skip_logs_error_and_deletes_nothing PASSED [ 44%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverConcurrentAput::test_real_saver_concurrent_aput_new_blob_preserved PASSED [ 55%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverRaceWindow::test_preexisting_referenced_blobs_survive_traffic_and_prune_byte_equal PASSED [ 66%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverSerializableRetry::test_real_40001_aborts_delete_then_retry_completes PASSED [ 77%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverDryRunReport::test_real_saver_dry_run_report_line_shape PASSED [ 88%]
tests/integration/checkpoint_prune_real_saver.py::TestRealSaverSqliteNoOp::test_real_saver_sqlite_backend_noops_with_warning PASSED [100%]

============================== 9 passed in 4.04s ===============================
```

**Result: 9 passed, 0 failed, 0 skipped — exit 0.** The SKIP-LOUDLY contract did not fire (PG reachable,
every test ran for real); ZERO skips counts as GREEN per T5.1. Matches the v1 `7a7998fe` baseline (9/9) and
the implementer's recorded runs (T5.1 + post-T5.19 re-run).

Supplementary reviewer-personal runs (same DSN discipline):
- `tests/integration/checkpoint_prune_restore_rehearsal.py` — **1/1 PASSED** (backup→prune→restore byte-equality, 2.29s).
- Council-side (cited per spec — "AST/import gates may be cited"): structural-unreachability AST gate 4/4 GREEN; full `test_maintenance_prune_direct_anti_join.py` 24/24 GREEN @ `e52d845e`.

---

## Section 5 — Verdict-Stamp Semantics

`APPROVED` ⟺ all three conjuncts:

| Conjunct | Evaluation | Result |
|---|---|---|
| (a) Every original finding RESOLVED | F1 🔴 — all three required folds present, citations verified against installed source, race tests meaningful (§2). F2 🟡 — bidirectional byte-equal coverage present and green. F3 🟡 — separate-pool topology present and exercised. No NOT-RESOLVED, no REGRESSED | ✅ TRUE |
| (b) 9/9 GREEN on REVIEWER re-run | §4: personally executed, 9 passed / 0 failed / 0 skipped @ `e52d845e`, disposable `ensemble_cpv2_test`, rev-parse bracketed | ✅ TRUE |
| (c) ZERO NEW findings from the port-only delta scan | Council: `f89ccacc→f8d59c01` patch-identical across all shared files (dual-method verification: normalized patch diff + `diff -u` extraction); `7a7998fe→a1ae0f91` patch-identical across all 5 shared files; every file-list asymmetry classified pure adaptation (`log_blob_prune` arrived earlier on v2 via PR1 port; manifest regen = v2 bookkeeping); tree-level `maintenance.py`/`constants.py` drift contains ZERO PR4/Operation-E/blob hunks (T5.19 wiring + baseline only). Class tally: every hunk (i) pure adaptation; **0 semantic drift; 0 v2-vocabulary hunks needed** | ✅ TRUE |

**⟹ Verdict: ✅ APPROVED.**

---

## Section 6 — Sign-off Fields

| Field | Value |
|---|---|
| Reviewer-instance ID | `reviewer[v2]` controller under tree-root `347fa33b-b135-4f20-b1f4-f61aad722924`; council governor spawned by this reviewer: `06ac38f1-3156-4d36-89e1-6e6ec5c33b8e` |
| Artifact commit SHA | `e2c15f99` — `docs(review): T5.7 PR4 re-review artifact — reviewer-authored, APPROVED, loop closed` (171 insertions, 1 file). This fill-in is post-commit bookkeeping per step 1c of the closure brief; the reviewer's verdict was recorded against `e52d845e` and is unchanged by this administrative edit. Reviewed code SHA: `e52d845e` |
| Verdict stamp | ✅ APPROVED (0 🔴 / 0 🟡 / 0 🟢 new findings from the port) |
| Reviewer model | Controller: `agentic` (session `OPENAI_MODEL`). Council: `agentic` + `coding` (2 councilors — see composition note) |

---

## Section 7 — Loop-Closed Semantics

APPROVED per §5 ⟹ **the v1 PR4 NEEDS_CHANGES loop is CLOSED.** No NOT-RESOLVED or REGRESSED entries exist;
no re-dispatch to the developer is required for PR4. The process gap (approval recorded only in a v1 commit
message) is now closed by this standalone reviewer-authored artifact.

Scope of closure: this approval closes the **review** loop. Operational destructive-enable remains governed
by `docs/runbooks/checkpoint-blob-prune-restore.md` (pre-enable checklist, prod `channel_versions`
verification, backup, 7-day dry-run soak) — and per the v1 finding's own semantics, the SERIALIZABLE wrap
narrows but does not eliminate the µs window; the §6 backup remains the recovery of record.

---

## Appendix — Method, Transparency, Residuals

**Council composition (transparency).** Requested models `agentic, coding, coding2`; `coding2` is not in
this system's allowed-models set and was skipped (no substitution). The resulting 2-model council
(`agentic` + `coding`) meets the multi-model consensus minimum and mirrors the v1 PR4 review's composition
(2 councilors, agentic + coding) — normal confidence, full convergence, zero disagreements, no refinement
rounds needed.

**Evidence provenance legend.** [REVIEWER-PERSONAL] = executed first-hand by this reviewer instance (gate
run, restore rehearsal, provenance checks, spec extraction, env hygiene). [COUNCIL] = static verification
by the convened council (file:line citations above are theirs; both councilors worked read-only —
`git show`/`git diff` only, no checkout/stash; installed-source reads from `.venv`). [CITED] = implementer
records accepted as context, never as the basis of this verdict.

**Residuals (disclosed, no action required for this verdict):**
1. Councilors did not re-run DB-touching suites (hard constraint — W4 serialization on the single
   disposable PG); the port's patch-identity with the v1-reviewed originals plus the reviewer's personal
   9/9 + 1/1 runs attest those suites.
2. "Verified on PG 14.22" claims inside the adapter wrap-rationale reference v1-session probe scripts not
   carried into the port commits — consistent with, though not re-derived by, this council; the live 40001
   retry test reproduces the mechanism on the installed stack regardless.
3. Dispatch context listed `tests/unit/services/test_checkpoint_blob_prune.py` — that path does not exist
   on this branch; the structural-unreachability AST gate actually lives at
   `tests/unit/services/test_maintenance_prune_direct_anti_join.py` (matrix item 6). Context drift only;
   no impact on the verdict.
