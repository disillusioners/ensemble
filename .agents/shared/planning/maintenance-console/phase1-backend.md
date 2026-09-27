# Phase 1 — Backend Work Plan: Maintenance Console · Section 1 (Checkpoint Cleanup)

Date: 2026-09-27 (amended same day — architect verdict READY-WITH-AMENDMENTS applied)
Author: plan-creation worker (Phase-1 detail pass); amended by plan-creation worker per `architecture-recommendation.md` AM-1…AM-18
Branch: `feature/maintenance-console` @ `666c089d`
Status: Ready for Review — **conforms to API Contract v3** ("API Contract v3 — frozen 2026-09-27" in `plan-overview.md`; v3 = reviewer fix pass over the architect-stamped v2 — C-1/C-2/W-1 + R-items applied). This satisfies re-freeze checklist item 3 (schema re-sync + new BE cases). Every amendment below carries an inline `[AM-n]` tag at its application point; no untagged design changes were made.
Spec source: `plan-overview.md` (API Contract v3 frozen 2026-09-27, Q1–Q4 post-amendment, Phase-1 9-task list, Mandatory Invariants INV-1…INV-13, Test Strategy → BE post-AM-16) + `architecture-recommendation.md` (authoritative amendments; Focus Areas 2/3/5/6) + `research-findings.md` §1, §2, §4 (§5.2's concurrency sketch is **superseded** — see T3). All file:line refs below were re-verified against the worktree at `666c089d`.

---

## 0. Invariants Carried (hard constraints — restated for implementers)

| # | Invariant | How this plan preserves it |
|---|---|---|
| INV-1 | Auto-cycle behavior/defaults UNCHANGED | `prune_unreferenced_blobs` kwarg defaults `destructive=None` → env dual-arm gate (Task 2). `CheckpointCleanupJob.execute()` op sequence A→E unchanged — **auto order stays D→E** (Task 1 only adds summary capture + return values; the E→D re-ordering [AM-2] lives ONLY in the manual entry point the auto cycle never calls). New job ctor kwargs are optional-`None` → legacy behavior when unwired (Task 3). New adapter method is read-only and auto never calls it (Task 2b). |
| INV-2 | Manual execute must NOT require env pre-arming | `destructive=True` explicit kwarg reaches the DELETE arm with env flags absent (Task 2, proven by test `test_destructive_true_reaches_delete_with_env_off`). The run row's `env_flags_json` records `{blob_prune_dry_run, blob_prune_destructive, destructive_override}` so the audit trail **proves the override kwarg — not env — armed the DELETE** [AM-15] (Task 5). |
| INV-3 | SERIALIZABLE+retry wrap and ZERO_REFS_FAIL_SAFE preserved | Task 2 touches ONLY how the `destructive` local is *fed*; the `if not destructive: continue` guard, the SERIALIZABLE wrap in the adapter's `delete_blobs_anti_join`, and the zero-refs skip are untouched. The existing AST pin `tests/unit/services/test_maintenance_prune_direct_anti_join.py::test_delete_call_is_structurally_gated_by_destructive_flag` must stay green (the local must remain named `destructive`). |
| INV-4 | Overlap guard designed | **Single-flight gate [AM-4 + AM-5]: per-run sequence `acquire asyncio.Lock → conditional INSERT (partial unique index ON (section) WHERE status='running') → run → finalize-in-finally`.** The DB claim is the real gate; INSERT conflict → 409 `run_in_flight` naming the in-flight run, NO row written (Task 3). Shared between auto cycle and manual path; auto lock-loss = non-raising skip + `last_run` update + DEBUG (Task 3.3). |
| INV-5 | Frozen contract is the Phase-2 seam | Phase 1 implements **API Contract v3** (frozen 2026-09-27). The §7 feedback items are RESOLVED — dispositions incorporated per the amended register; no open contract debt remains from this file (INV-5 satisfied for Phase 1). |
| INV-6 | Capture `BlobPruneSummary`; assemble Op D structured counts | Task 1 (`maintenance.py:934` discard → return value; `CheckpointRowPruneSummary` + `CheckpointRunResult`). The dry-run arm gains canonical accumulation fields `would_delete_count`/`would_free_bytes` [AM-11] (currently discarded, `checkpoint_prune.py:203-217`). |
| INV-7 | Every new timestamp bind uses `now_utc_naive()`; **TEXT ISO columns use `now_utc_iso()`** | Task 4 model columns are **TEXT ISO** (`now_utc_iso()`, `daemon/services/timestamps.py:63-70` — emits `+00:00`) per [AM-15] — zero `sa.DateTime` columns, sidestepping the naive/tz trap entirely (repo has no `sa.DateTime` precedent). Naive-UTC binds (`now_utc_naive()`) apply where in-memory datetime math is needed (freshness age check, Task 5). Wire serialization via `now_utc_iso()`/`to_utc_iso()` (`daemon/services/timestamps.py:76`). No tz-aware binds anywhere. |
| INV-8 | PG-safe persistence; zero-migration not chosen (operator audit genuinely needs a table) | Task 4: SQLModel + `SQLModel.metadata.create_all` on **BOTH drivers** (fresh AND existing PG, and SQLite) + `-- MANUAL: TRUE` .sql as canonical doc only (runner no-ops on PG, `runner.py:693-730`). **NO `_ensure_postgres_columns` entries** [AM-15] — that path is for existing-table evolution; new tables need none (snapshot precedent verbatim, `manager.py:530-546`) [R-3]. Cannot regress fresh-SQLite boot (the 20260714 trap class is structurally unreachable — no migration file is ever applied by the runner). |
| INV-9 | **Manual execute composes Op E → Op D; auto-cycle order unchanged** [AM-2] | The manual entry point (`run_checkpoint_prunes`, Task 1.7) runs blobs-before-rows; `CheckpointCleanupJob.execute()` keeps its inline D→E sequence (Task 1.7 note + rationale from `decision-log.md` AM-2 rationale). Regression pins: Task 9 cases 44/57 — execute with excess rows present must NOT over-delete. Under D-first **every server check passes** (the byte-equality gate compares echo vs stored dry-run — always equal), so the failure is SILENT over-deletion beyond the confirmed byte echo, caught only by these regression cases [W-1, v3 fix pass]. |
| INV-10 | **`require_trusted_origin` is the FIRST check on `/status`, `/dry-run`, `/execute`, `/runs/{id}`; `/availability` exempt** [AM-1] | Router-level dependency ordered before the kill-switch and every other gate (Task 6.2). Sole browser-borne defense layer in v1 (CORS `allow_origins=["*"]` + `allow_credentials=True`, `api.py:2613-2619`, makes non-credentialed cross-origin response bodies readable). |
| INV-11 | **Boot sweep unconditionally CAS `running → interrupted` at lifespan start; no live watchdog in v1** [AM-7] | Task 8: rowcount-guarded CAS at lifespan start (PlaneSync `fail_stale_syncing` pattern, `plane_sync_watchdog_service.py:299-316`), no age gate, one summary log line. |
| INV-12 | **`maintenance_runs` schema follows the conventions-validated form; `skipped[]` capped at 1000 + `skipped_truncated:true`** [AM-15] | Task 4 implements the FINAL schema verbatim (TEXT PK, TEXT ISO timestamps, JSONBType, partial unique claim index + composite `(section, completed_at)`); Task 1.1/T5 enforce the skipped-cap. |
| INV-13 | **NO server-side actual-vs-expected completion gate** [W-1, v3 fix pass] | The byte-equality check (AM-3, T5.4 step 6) is a PRE-RUN scope pin only — echoed `expected_bytes` vs the STORED dry-run row. No post-run comparison of actual deleted bytes vs `expected_bytes` may be added: benign in-window drift (new skip pairs, live-app writes) would false-refuse completed runs. The AM-2 E-first ordering keeps actual == expected on the happy path; the D-first failure mode (silent over-deletion beyond the confirmed echo) is caught by regression cases 44/57, never by a gate. |

---

## 1. Component Inventory (at a glance)

**New files**

| Path | What |
|---|---|
| `daemon/routers/maintenance_origin_guard.py` | `require_trusted_origin` FastAPI dependency — ONE guard function, source-grep pinnable [AM-1] |
| `daemon/services/maintenance_run_identity.py` | `new_maintenance_run_id()` — `ckpt-YYYYMMDD_HHMMSSffffff-hex8`, colon-free/URL-clean, `migration_id` precedent (`daemon/routers/migration.py:112-114`) [AM-8] |
| `daemon/services/maintenance_run_lock.py` | `MaintenanceRunContext`, `MaintenanceRunLock` (in-process step 1 of the single-flight gate; DB claim is the real gate) [AM-4] |
| `daemon/services/maintenance_boot_sweep.py` | `sweep_interrupted_running_runs()` — unconditional rowcount-guarded CAS `running → interrupted` at lifespan start [AM-7] |
| `daemon/services/maintenance_api_service.py` | `MaintenanceApiService` (manual orchestration; composes Op E → Op D [AM-2]), `MaintenanceError` |
| `daemon/repositories/maintenance_runs/__init__.py` | package |
| `daemon/repositories/maintenance_runs/models.py` | `MaintenanceRun` SQLModel — FINAL schema per Focus Area 5 [AM-15] |
| `daemon/repositories/maintenance_runs/repository.py` | `MaintenanceRunsRepository` (sync, `asyncio.to_thread` bridged by callers — snapshot-repo pattern) |
| `daemon/routers/maintenance.py` | 5 frozen endpoints + `get_maintenance_api_service` Depends getter + kill-switch mapping [AM-13] |
| `daemon/migrations/versions/20260927_000001_create_maintenance_runs_table.sql` | canonical DDL, `-- MANUAL: TRUE` (doc only — table lands via `create_all` on both drivers) [AM-15] |
| `tests/unit/services/test_checkpoint_prune_destructive_override.py` | kwarg unit suite |
| `tests/unit/services/test_maintenance_checkpoint_cleanup_service.py` | service-layer unit suite (incl. Origin guard matrix [AM-16]) |
| `tests/unit/services/test_maintenance_run_lock_and_capture.py` | gate + summary-capture unit suite |
| `tests/integration/test_maintenance_checkpoint_cleanup_api.py` | disposable-PG API suite |

**Modified files**

| Path | Change |
|---|---|
| `daemon/services/maintenance.py` | Task 1 (summaries + AM-2 ordering in the manual entry point), Task 3 (job gate/repo kwargs + auto row + lock-loss skip semantics), `is_idle()` public wrapper |
| `daemon/services/checkpoint_prune.py` | Task 2 (`destructive` kwarg), Task 1 (`BlobPruneSummary` dry-run accumulation fields `would_delete_count`/`would_free_bytes` [AM-11]) |
| `daemon/checkpoint_adapter.py` | Task 2b (`count_writes_excluding` — abstract + SQLite + PG) |
| `daemon/routers/schemas.py` | Task 6 (`CheckpointCleanup*` pydantic models, appended; **no `idempotency_key` field** [AM-17]) |
| `daemon/api.py` | Task 6 (import + `include_router` in the 2687–2726 block; `app.state.maintenance_api_service` after `manager.initialize()`, ~:423); Task 8 (boot-sweep call inside the lifespan, `api.py:202`, post-`manager.initialize()` pre-serve) |
| `daemon/manager.py` | Task 5 wiring (~2603–2631): gate, repo, service construction; model import before `SQLModel.metadata.create_all` (:530–547) — **NO `_ensure_postgres_columns` entries** [AM-15]; shutdown entry (~:11564) |
| `daemon/constants.py` | Task 7 (`MAINTENANCE_DRY_RUN_FRESH_SECONDS=300` [§6.4 CONFIRMED], `MAINTENANCE_ENDPOINTS_ENABLED=True` default 1, boot-read [AM-13], `MAINTENANCE_TRUSTED_ORIGINS=""` CSV default empty [AM-1], `CHECKPOINT_MAX_PER_THREAD_FLOOR=1`) |
| `daemon/config.py` | Task 7 (`ge=CHECKPOINT_MAX_PER_THREAD_FLOOR` — single source for the floor) |
| `tests/integration/test_checkpoint_cleanup_job_wiring_pin.py` | Task 9 (pin the two new ctor kwargs) |

---

## 2. Task Breakdown

Dependency graph (letters map to the tasks below; **task list restructured post-amendment: was T1–T8 with T8 = integration suite; now T1–T9 with T8 = boot sweep [AM-7] and T9 = integration suite + wiring pins**):

```
T1 (summaries + AM-2 manual ordering) ──┐
T2 (kwarg) ─────────────────────────────┼──> T5 (api service) ──> T6 (router + origin guard + schemas + kill-switch) ──> T9 (integration suite)
T2b (adapter) ──────────────────────────┤                                                                       (wiring pin rides T9)
T3 (single-flight gate + auto wiring) ──┤
T4 (persistence FINAL schema + run_id) ─┘
T7 (constants/config) ──> T5/T6 (consumed)
T4 ──> T8 (boot sweep, lifespan start) ──> T6/T9 (wired + proven)
```

T1, T2, T2b, T3, T4 are mutually independent and can be developed/committed in parallel. T5 depends on all of them. T6 depends on T5 (+T7). T8 depends on T4. T9's unit parts ride along with each task; its integration part lands last.

---

### T1 — Capture BlobPruneSummary + Op D structured counts (`CheckpointRunResult`) + manual ordering [AM-2, AM-10, AM-11]

**Files:** `daemon/services/maintenance.py`, `daemon/services/checkpoint_prune.py`

1.1 **`BlobPruneSummary` dry-run accumulation** (`daemon/services/checkpoint_prune.py:87-101`). The current dry-run arm (`:203-217`) computes `count_blobs_anti_join` results but only logs them — `summary.total_deleted/total_bytes_freed` accumulate in the DESTRUCTIVE arm only (`:235-236`). Add two fields with the **canonical names ratified by A-2/AM-11**:

```python
would_delete_count: int = 0   # dry-run arm accumulation [AM-11]
would_free_bytes: int = 0
```

and in the dry-run arm (`:203-217`), after `log_blob_prune`, add `summary.would_delete_count += would_delete; summary.would_free_bytes += bytes_would_free`. Destructive arm untouched (fields stay 0). Backward compatible (new fields default 0; no existing consumer reads them).

1.2 **`CheckpointRowPruneSummary`** (new dataclass in `maintenance.py`, next to `CheckpointCleanupJob`):

```python
@dataclass
class CheckpointRowPruneSummary:
    backend: str = "postgres"
    scanned_pairs: int = 0            # len(find_all_thread_ns_pairs()) — ALL groups
    excess_pairs: int = 0             # len(find_excess_checkpoint_groups(N))
    deleted_checkpoints: int = 0      # destructive: sum(delete_checkpoints_excluding)
    deleted_writes: int = 0           # destructive: sum(delete_writes_excluding)
    would_delete_checkpoints: int = 0 # dry-run: sum(cnt - N)
    would_delete_writes: int = 0      # dry-run: sum(count_writes_excluding)  (T2b)
```

1.3 **`CheckpointRunResult`** (new dataclass in `maintenance.py`):

```python
@dataclass
class CheckpointRunResult:
    rows: CheckpointRowPruneSummary = field(default_factory=CheckpointRowPruneSummary)
    blobs: BlobPruneSummary = field(default_factory=BlobPruneSummary)
    duration_ms: int = 0
    skipped_truncated: bool = False   # [AM-10/AM-15] set when skipped[] hit the 1000-entry cap
    def to_summary_dict(self) -> dict[str, Any]:
        """The FROZEN /status last_run.summary wire shape (plan-overview API Contract v3 §2)."""
```

`to_summary_dict` emits exactly the Contract-v2 shapes: `checkpoint_rows: {scanned_pairs, deleted, excess_pairs}`, `writes: {deleted}`, `duration_ms`, and `blobs` per flavor [AM-11 — dual-flavor keys, FE branches on `destructive`]:
- **Dry-run flavor** (`destructive: false`, mirrors the overview §2 `/status` example): `blobs: {scanned_pairs, would_delete_count, would_free_bytes, would_delete, bytes, destructive, skipped}` — both the canonical fields and the `would_delete`/`bytes` symmetry keys.
- **Destructive flavor** (`destructive: true`): `blobs: {scanned_pairs, deleted, bytes_freed, destructive, skipped}`.
- `skipped` entries serialized as `[{thread_id, checkpoint_ns, reason}]` objects (tuples are not JSON-safe in `summary_json`) [AM-10], **capped at 1000 entries with `skipped_truncated: true` when the cap fires** [AM-10/AM-15].

1.4 **Op D returns a summary.** `_prune_per_thread_checkpoints` (`maintenance.py:793`, currently `-> None`) → `-> CheckpointRowPruneSummary`. Keep every existing log line byte-identical; add:
- `scanned_pairs = len(await self._checkpointer.find_all_thread_ns_pairs())` — ONE extra read-only GROUP BY per cycle (24h cadence; no delete-behavior change — INV-1 intact). Rationale: the contract example shows `scanned_pairs: 12` with `excess_pairs: 0`, which is only consistent with "all groups enumerated".
- `deleted_writes` accumulates the existing `delete_writes_excluding` return alongside the existing `observed_total_deleted` (same live-accumulation pattern, W7).
- Early-return branch (`:869-872`, no excess) returns a summary with `scanned_pairs` populated, zeros elsewhere.

1.5 **Op D read-only arm (manual dry-run).** New method `_compute_row_prune_dry_run(self) -> CheckpointRowPruneSummary`: `find_all_thread_ns_pairs()` (scanned), `find_excess_checkpoint_groups(N)` (excess), per excess pair `get_checkpoint_ids(N)` → `would_delete_checkpoints += cnt - N` and `would_delete_writes += await count_writes_excluding(thread, ns, keep_ids)` (T2b). Zero DELETE statements.

1.6 **Op E returns its summary.** `_prune_unreferenced_blobs` (`maintenance.py:909-934`) → `-> BlobPruneSummary`; `:934` becomes `return await prune_unreferenced_blobs(self._checkpointer)` (the discard fixed — INV-6). Add optional pass-through kwarg `_prune_unreferenced_blobs(self, *, destructive: bool | None = None)` forwarding to `prune_unreferenced_blobs(self._checkpointer, destructive=destructive)` (auto call sites pass nothing → env gate).

1.7 **Manual D+E entry point — composes Op E → Op D [AM-2, BLOCKING].** New `CheckpointCleanupJob.run_checkpoint_prunes(self, *, destructive: bool) -> CheckpointRunResult` — UNLOCKED (caller owns the gate). **This entry point is MANUAL-ONLY (dry-run + execute); the auto `execute()` keeps its own inline A→E sequence with the auto-cycle D→E order UNCHANGED (INV-1, INV-9).** [R-19, v3 fix pass] The method docstring carries a literal **`MANUAL-ONLY`** tag, and an **AST pin** (in the wiring-pin suite) asserts the auto `execute()` body contains NO call to `run_checkpoint_prunes` — the auto cycle must never route through the manual entry point. The ordering implements the architect's blocking correctness fix (`decision-log.md` AM-2 rationale; `architecture-recommendation.md` Focus Area 2):

```python
async def run_checkpoint_prunes(self, *, destructive: bool) -> CheckpointRunResult:
    # [AM-2, mechanism restated W-1 v3] MANUAL path composes Op E (blobs)
    # BEFORE Op D (rows). The dry-run's anti-join counts blobs referenced
    # by ANY remaining checkpoint row (checkpoint_prune.py:12-17). Under
    # D-first every execute check PASSES — the byte-equality gate (AM-3)
    # compares the echo against the STORED dry-run row, so it always
    # matches — and the failure is SILENT over-deletion: Op D unreferences
    # blobs the subsequent blob pass then deletes beyond the confirmed
    # echo. E-first keeps actual == expected; auto-cycle keeps D→E (INV-1).
    # NEVER add a post-run actual-vs-expected completion gate (INV-13).
    t0 = time.perf_counter()
    blobs = await self._prune_unreferenced_blobs(
        destructive=destructive if destructive else False
    )
    rows = (await self._prune_per_thread_checkpoints()) if destructive \
           else (await self._compute_row_prune_dry_run())
    return CheckpointRunResult(rows=rows, blobs=blobs,
                               duration_ms=int((time.perf_counter() - t0) * 1000))
```

Note the explicit `destructive=False` for the manual dry-run blob arm: the manual dry-run must be dry-run **even when the operator has the env dual-arm armed** (env default is dry-run, but an armed env must not leak destructivity into the manual preview). The dry-run's E→D order mirrors the destructive manual order for preview fidelity (both dry-run arms are read-only; ordering is immaterial for correctness there, but the preview should not lie about the order it will run in). Residual cost of E-first (accepted, `decision-log.md`): blobs referenced only by excess rows survive one extra cycle — conservative under-delete, self-healing.

1.8 **`execute()` (auto) assembles its own summary.** `execute()` (`maintenance.py:431`) captures Op D + Op E results into a `CheckpointRunResult` (ops A–C remain log-only; not in the contract shape) and hands it to the auto run-row write (T3.3). Return type stays `-> None` (callers at `MaintenanceService._run_pending_jobs:236` ignore returns; no signature ripple).

**Acceptance:**
- `maintenance.py:934` no longer discards the summary (grep pin: `await prune_unreferenced_blobs(` must be assigned/returned).
- Existing logs unchanged (log-assert pins from `tests/unit/services/test_maintenance_prune_direct_anti_join.py` stay green).
- New unit tests (§4.1) prove: dry-run accumulation fields populated (canonical names); `to_summary_dict` shape pins for BOTH flavors + skipped-cap/truncation flag; `run_checkpoint_prunes(destructive=False)` performs ZERO delete calls with a mock adapter; **ordering pin — the manual entry point awaits the blob arm before the row arm** (call-order assertion, AM-2).

**Depends on:** nothing. **Blocks:** T5.

---

### T2 — `destructive` override kwarg on `prune_unreferenced_blobs`

**Files:** `daemon/services/checkpoint_prune.py`

2.1 Signature (`:104-108`):

```python
async def prune_unreferenced_blobs(
    checkpointer,
    *,
    max_refs_per_thread: int = CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD,
    destructive: bool | None = None,
) -> BlobPruneSummary:
```

2.2 The gate feed (`:132-133`) becomes:

```python
if destructive is None:
    destructive = blob_prune_destructive_enabled()   # env dual-arm — AUTO path, unchanged
summary.dry_run = not destructive
```

Semantics: `None` (default — every existing call site incl. the auto cycle) → env dual-arm `blob_prune_destructive_enabled()` exactly as today; `False` → forced dry-run (manual preview; overrides an armed env); `True` → destructive (manual execute; **no env pre-arming required** — INV-2). The env dual-arm gate function `blob_prune_destructive_enabled()` (`:71-84`) itself is untouched.

2.3 **AST-pin compatibility (critical).** The structural pin `test_delete_call_is_structurally_gated_by_destructive_flag` (`tests/unit/services/test_maintenance_prune_direct_anti_join.py:112-194`) requires the DELETE call to be dominated by `if not destructive: ... continue` where the test is `ast.Name(id="destructive")`. The local variable name `destructive` is PRESERVED (parameter shadowed into the local at the gate feed) and the guard at `:203` is untouched → pin stays green. `test_source_contains_exactly_one_delete_call_site` (`:196-203`) also unaffected (still exactly one call site). Update the module docstring (`:26-46`, invariant 3) to document the override: "the destructive arm is reachable when `blob_prune_destructive_enabled()` holds OR the caller passes `destructive=True` explicitly (Maintenance API manual path only)."

2.4 **Env-dual-arm auto pin (separate test).** With `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` but `CHECKPOINT_BLOB_PRUNE_DRY_RUN=1` (one arm only), a no-kwarg call stays dry-run — proving the dual-arm is not loosened (INV-1).

**Acceptance:**
- `prune_unreferenced_blobs(adapter)` (no kwarg) + env OFF → dry-run (existing `test_runtime_gate_off_delete_never_called` green).
- `prune_unreferenced_blobs(adapter, destructive=True)` + env OFF → DELETE arm reached (exploding-sentinel mock observes exactly one `delete_blobs_anti_join` await) — INV-2 proof.
- `prune_unreferenced_blobs(adapter, destructive=False)` + BOTH env flags armed → dry-run (kwarg wins over env for the manual preview).
- `prune_unreferenced_blobs(adapter, destructive=True)` + ZERO_REFS staged → pair skipped, zero deletes (INV-3; mock-based, per the existing zero-refs test shape).
- AST dominance pin + single-call-site pin green unmodified.

**Depends on:** nothing. **Blocks:** T5. **Note:** T1.1 and T2 touch adjacent regions of the same file — land as one commit to avoid text conflicts.

---

### T2b — Read-only writes accounting on the adapter

**Files:** `daemon/checkpoint_adapter.py`

3.1 New abstract method (after `delete_writes_excluding`, `:107-114`):

```python
@abstractmethod
async def count_writes_excluding(
    self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
) -> int:
    """DRY-RUN arm of the Op D writes accounting — SELECT COUNT only.

    Read-only mirror of delete_writes_excluding. Used ONLY by the manual
    dry-run path; the auto cycle never calls it (INV-1).
    """
```

3.2 **PG impl** (`PostgresCheckpointerAdapter`, after `:534`):

```sql
SELECT COUNT(*) FROM checkpoint_writes
WHERE thread_id = $1 AND checkpoint_ns = $2
AND NOT (checkpoint_id = ANY($3::text[]))
```

(note `checkpoint_writes`, per the naming NOTE at `:513-515`).

3.3 **SQLite impl** (mirror shape, `writes` table, `?` placeholders — table name per the SQLite side, cf. `:290-308`).

**Acceptance:** unit test with the disposable-PG harness stages W writes, keeps N checkpoints, asserts `count_writes_excluding == delete_writes_excluding` return for the same keep-set (read-only/destructive parity pin), and asserts zero rows removed after the count call.

**Depends on:** nothing. **Blocks:** T1.5, T5.

---

### T4 — `maintenance_runs` persistence (FINAL schema, model, repo, canonical DDL) [AM-15]

**Files:** `daemon/repositories/maintenance_runs/{__init__,models,repository}.py`, `daemon/migrations/versions/20260927_000001_create_maintenance_runs_table.sql`, `daemon/manager.py`, `daemon/services/maintenance_run_identity.py`

4.1 **`MaintenanceRun` SQLModel** (`models.py`) — **the FINAL schema from `architecture-recommendation.md` Focus Area 5, verbatim** (replaces the prior sketch and research §4.1):

| column | type | notes |
|---|---|---|
| `run_id` | TEXT PK | `ckpt-<YYYYMMDD_HHMMSSffffff>-<hex8>` [AM-8]; externally-facing id, also the polling key |
| `section` | TEXT NOT NULL, default `"checkpoint-cleanup"` | v1 constant; future sections reuse the table |
| `kind` | TEXT NOT NULL | `auto` / `manual_dry_run` / `manual_execute` |
| `started_at` | **TEXT NOT NULL** — `now_utc_iso()` | TEXT ISO (`+00:00`), INV-7/AM-15 — zero `sa.DateTime` columns |
| `completed_at` | **TEXT NULL** — `now_utc_iso()` on terminal write | NULL while running |
| `status` | TEXT NOT NULL, default `"running"` | **`running` / `succeeded` / `failed` / `interrupted`** [AM-6 — `overlap_refused` DELETED from the schema and the audit story] |
| `triggered_by` | TEXT NOT NULL | `"system"` (auto) / `"user"` (manual; no session ids exist) |
| `requester_json` | JSONBType NULL | `{peer_ip, user_agent, origin}` — forensics, never attribution; NULL for auto [AM-15] |
| `dry_run_run_id` | TEXT NULL | soft ref, no FK (dialect-divergent cascade trap) [AM-15] |
| `expected_bytes` | INTEGER NULL | echoed promise [AM-15] |
| `dry_run_summary_json` | JSONBType NULL | full dry-run snapshot (incl. `skipped[]`) — self-contained audit [AM-15] |
| `confirm` | BOOLEAN NULL | [AM-15] |
| `advisory` | TEXT NULL | `'system_busy'` \| NULL [AM-12/AM-15] |
| `env_flags_json` | JSONBType NULL | `{blob_prune_dry_run, blob_prune_destructive, destructive_override}` — proves INV-2 (override kwarg, not env, armed the DELETE) [AM-15] |
| `summary_json` | JSONBType NULL | outcome: BlobPruneSummary + Op D counts + duration_ms (`CheckpointRunResult.to_summary_dict()` or dry-run payload) |
| `error_json` | JSONBType NULL | `{code, message}` |

JSON columns use `JSONBType` (`daemon/repositories/infra/types.py:35` [R-3 cite fix — was `:32-60`] — JSONB on PG / JSON on SQLite; raw PG `JSONB` breaks SQLite `create_all`) [AM-15].

`__table_args__` [AM-5 + AM-15]:

```python
__table_args__ = (
    Index(
        "uq_maintenance_runs_running_section",
        "section",
        unique=True,
        postgresql_where=text("status = 'running'"),   # dual-dialect where-clauses
        sqlite_where=text("status = 'running'"),       # so create_all builds it on BOTH drivers
    ),                                                  # ← the DB single-flight claim (AM-5)
    Index("ix_maintenance_runs_section_completed", "section", "completed_at"),
)
```

**Phase-1 verification step (T9 case 64):** introspect `create_all` render on a disposable PG AND a file-backed SQLite — the partial unique index must exist on both (`sqlalchemy.inspect(...).get_indexes` with `unique=True` and the where-clause preserved on PG; SQLite accepts partial indexes natively ≥3.8). **Documented fallback** if the dual-dialect render fails on SQLite: plain conditional `INSERT … SELECT … WHERE NOT EXISTS (SELECT 1 FROM maintenance_runs WHERE section=? AND status='running')` checked in the same transaction — with the **racy-belt caveat** (two concurrent inserters can both pass the NOT EXISTS check; the in-process asyncio.Lock remains the primary serialization, the DB claim becomes belt) — documented in the module docstring, not silently substituted [AM-5].

4.2 **`MaintenanceRunsRepository`** (`repository.py`) — sync methods, engine-injected ctor (snapshot-repo pattern; callers bridge via `asyncio.to_thread`):

- `insert(run: MaintenanceRun) -> bool` — returns `False` on unique-claim conflict (IntegrityError on `uq_maintenance_runs_running_section`) → caller raises 409 `run_in_flight`, **NO row written** [AM-5]. One retry on PK collision for the hex8 suffix (regenerate via `new_maintenance_run_id()`).
- `get(run_id: str) -> MaintenanceRun | None`
- `get_running(section: str) -> MaintenanceRun | None` — the at-most-one `running` row (the partial index guarantees uniqueness); source for `in_flight` [AM-9: `in_flight` = any `running` row] and for 409 body forensics.
- `latest_completed_for_section(section: str, kinds: tuple[str, ...] = ("auto", "manual_execute")) -> MaintenanceRun | None` — filters `status IN ('succeeded','failed')`, `ORDER BY completed_at DESC LIMIT 1` [AM-9: `last_run` = latest `succeeded|failed` of `kind ∈ {auto, manual_execute}`; `manual_dry_run` NEVER surfaces; dry-run history is queryable via `GET /runs/{id}`]. TEXT ISO timestamps sort lexicographically; filtering to terminal statuses still sidesteps PG's `NULLS FIRST` default on `DESC` and matches the contract (running runs surface via `in_flight`, not `last_run`).
- `get_dry_run(run_id: str) -> MaintenanceRun | None` — `kind='manual_dry_run'` lookup for execute validation.
- `mark_terminal(run_id, status, completed_at, summary_json=None, error_json=None) -> None` — `completed_at`/timestamps as TEXT ISO (`now_utc_iso()`).
- `cas_running_to_interrupted(section: str) -> int` — **boot-sweep CAS** [AM-7]: `UPDATE maintenance_runs SET status='interrupted', error_json='{"code": "run_interrupted", ...}', completed_at=<sweep time now_utc_iso()> WHERE section=:section AND status='running'` → returns affected rowcount (the rowcount guard). Unconditional trigger — NO age gate (a boot-time `running` row is an orphan by definition under the single-daemon assumption; an age gate would orphan young rows).

4.3 **Canonical DDL** `20260927_000001_create_maintenance_runs_table.sql`: the Focus Area 5 DDL verbatim (incl. both indexes), dual-dialect-safe column tokens (TEXT / INTEGER / BOOLEAN / JSON), `-- MANUAL: TRUE` header marker (parsed at `daemon/migrations/runner.py:113`) — **canonical doc only**: the runner is a NO-OP on PG (`runner.py:693-730`) and the table is built by `SQLModel.metadata.create_all` on **BOTH drivers** at boot (`manager.py:546`; registration block `manager.py:530-546`) [AM-15] [R-3 cite fix]. Rollback section = `DROP TABLE IF EXISTS maintenance_runs;`.

4.4 **Activation paths** [AM-15 — supersedes the prior `_ensure_postgres_columns` mirror design]:
- **All drivers:** import `MaintenanceRun` in the manager's model-registration block (`daemon/manager.py:530-547`, alongside the snapshot models — same precedent: "no `_ensure_postgres_columns` mirror for brand-new tables") BEFORE `SQLModel.metadata.create_all(self._engine)` (`:546`) [R-3 cite fix — create_all is at manager.py:546].
- **NO `_ensure_postgres_columns` entries** — that path is for existing-table evolution; new tables need none. **Cannot regress fresh-SQLite boot**: the 20260714 trap class (PG-only `DROP CONSTRAINT` syntax applied by the SQLite runner) is structurally unreachable here — no migration file is ever applied by the runner, and `create_all` is dialect-agnostic.
- SQLite: the runs table IS created (both-driver create_all [AM-15]) — the PG-only constraint concerns the checkpoint-blob prune, not this audit table. `/availability` still reports `backend_unsupported` there (T5.4).

**Acceptance:** on a disposable PG AND a file-backed SQLite: `create_all` creates table + partial unique index + composite index (case 64); repo round-trip stores/reads JSON columns as dicts; TEXT ISO timestamps round-trip as strings; `latest_completed_for_section` ordering + kinds filter proven with seeded rows (running, succeeded, failed, manual_dry_run); `cas_running_to_interrupted` flips only `running` rows and returns the count.

**Depends on:** nothing. **Blocks:** T3, T5, T8.

---

### T3 — Single-flight gate (`MaintenanceRunLock` + conditional INSERT) + auto-cycle integration [AM-4, AM-5, AM-6]

**Files:** NEW `daemon/services/maintenance_run_lock.py`, `daemon/services/maintenance_run_identity.py`, `daemon/services/maintenance.py`, `daemon/manager.py`

5.1 **`MaintenanceRunContext` + `MaintenanceRunLock`** (`maintenance_run_lock.py`):

```python
@dataclass
class MaintenanceRunContext:
    run_id: str
    kind: str                 # 'auto' | 'manual_dry_run' | 'manual_execute'
    started_at: str           # now_utc_iso() — TEXT ISO (INV-7/AM-15)
    triggered_by: str         # 'system' | 'user'

class MaintenanceRunLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current: MaintenanceRunContext | None = None

    @property
    def in_flight(self) -> MaintenanceRunContext | None: ...
    async def acquire(self, ctx: MaintenanceRunContext) -> bool: ...   # FAIL-FAST
    def release(self) -> None: ...
```

**The lock is step 1 of a per-run sequence; the DB claim is the real gate [AM-4 + AM-5].** Full sequence per run (auto or manual): `acquire asyncio.Lock → conditional INSERT (partial unique index) → run → finalize-in-finally`.

- **Fail-fast lock:** `acquire` returns `False` immediately when held (no await-queueing) — the auto path must not stall the generic `MaintenanceService._loop` behind a multi-minute manual run. `locked()`-check → `await acquire()` has no intervening await point, so it is race-free on a single event loop. `in_flight` returns the holder's context or `None`.
- **Supersession note [AM-4]:** this **supersedes research-findings §5.2's "live status row written BEFORE acquire"** — that ordering has a crash window producing a phantom `running` row that 409s the whole feature until manual DB surgery. Row-after-acquire + finalize-in-finally closes it; the boot sweep (T8) is the residual-crash backstop.
- **Two-dev-daemons-on-shared-PG class [AM-5]:** the partial unique index kills it — not corruption (per-pair SERIALIZABLE+retry + RETURNING accounting + idempotent deletes keep two pruners *correct*) but double-scan churn and divergent audit totals. `pg_advisory_xact_lock` was REJECTED by the architect: it pins a pool connection for minutes against the shared 15-conn pool.
- **Conflict semantics [AM-5]:** INSERT conflict on `uq_maintenance_runs_running_section` → 409 `run_in_flight` with `details.run_id` + `details.started_at` (NESTED under `details` [C-2, v3 fix pass]) naming the IN-FLIGHT run (read via `get_running`), **NO row written** for the refused caller.
- **No refusal rows [AM-6]:** `overlap_refused` is DELETED from the status enum and the audit story (architect ruling: 404-noise churn from double-clicks outweighs attempt-audit value). Every refused acquire produces **one INFO log line with requester forensics** (run_id attempted, kind, peer_ip, user_agent, origin, in-flight run_id) — nothing else.
- **No cancel endpoint in v1 [AM-7/Focus Area 3; leader ruling 3]:** no abort exists in the prune loop today (single per-pair `for` with unconditional `continue`s, `checkpoint_prune.py:152-258`). The **v2 insertion point is the per-pair loop top** — a cooperative `asyncio.Event` set by a future cancel endpoint, ~10 lines. v1 recourse for a wedged run = daemon restart → boot sweep (T8). Prune is retention-idempotent: re-run converges; per-pair independence means a partial pass finishes next run — resume is rejected by design.

5.2 **`new_maintenance_run_id()`** (`maintenance_run_identity.py`) [AM-8]: `f"ckpt-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}-{secrets.token_hex(4)}"` → shape `ckpt-20260927_032000123456-2a18f3c9` — **colon-free (URL-clean), lexicographically sortable, aligned with the `migration_id` precedent (`daemon/routers/migration.py:112-114`)**; hex8 (not hex4) per Focus Area 1 defense-in-depth (`/runs/{id}` enumeration hardening).

5.3 **`CheckpointCleanupJob` auto wiring** (`maintenance.py`): new optional ctor kwargs `run_lock: MaintenanceRunLock | None = None`, `runs_repo: MaintenanceRunsRepository | None = None` (both default `None` → today's exact behavior; existing unit tests that construct the job unwired are untouched). `execute()`:

```
if self._run_lock is not None:
    ctx = MaintenanceRunContext(new_maintenance_run_id(), 'auto', now_utc_iso(), 'system')
    if not await self._run_lock.acquire(ctx):
        # [AM-4/Focus Area 6] auto-tick losing the gate = NON-RAISING skip +
        # last_run update + DEBUG with the in-flight run_id.
        logger.debug("checkpoint_cleanup skipped: maintenance run %s in flight",
                     in_flight_run_id)
        job.last_run = utcnow()        # existing field (maintenance.py:72, 237-241):
                                       # re-arms the interval; NOT a failure retry
        return                         # no ops, NO row (AM-6: no refusal rows)
try:
    if self._runs_repo is not None:
        # [AM-5] conditional INSERT — conflict → skip path above is unreachable here
        # (lock is held), but the insert remains conflict-guarded for belt-and-braces.
        insert row(kind='auto', status='running', triggered_by='system',
                   run_id=ctx.run_id, env_flags_json=<current env dual-arm state>)
    ... existing ops A→E (D→E order unchanged — INV-1/INV-9), capturing Op D + Op E
        results into CheckpointRunResult (T1.8) ...
    if self._runs_repo is not None:
        mark_terminal(ctx.run_id, 'succeeded', now_utc_iso(), summary_json=result.to_summary_dict())
finally:
    if self._run_lock is not None: self._run_lock.release()
```

On exception: row → `failed` + `error_json` (infra faults only — pair failures live in `summary.skipped`), then re-raise into the existing per-op isolation semantics (the job's ops already never raise; the outer guard is belt-and-braces per `:472-481`).

5.4 **Manager wiring** (`daemon/manager.py:2603-2631`): construct `MaintenanceRunLock()` + `MaintenanceRunsRepository(self.engine)` once; pass BOTH into the `CheckpointCleanupJob(...)` construction (`:2614-2626`) — same single construction site the wiring pin guards — and hold them for T5's service.

**Acceptance:**
- Unit: lock acquire/release/in_flight semantics incl. fail-fast (no deadlock under a held lock; `asyncio.wait_for(acquire, 0.1)` returns `False`).
- Unit: **conditional-INSERT conflict path** — repo `insert` against a seeded `running` row returns `False` (or raises the conflict sentinel) with NO second row persisted [AM-5].
- Unit: `execute()` with `run_lock` held externally → returns without running ops, DEBUG logged with the in-flight run_id, `job.last_run` updated, NO row written [AM-4/AM-6].
- Unit: `execute()` with `runs_repo` wired → row lifecycle running→succeeded with `kind='auto'`, `triggered_by='system'`, `env_flags_json` stamped.
- Unit: unwired job (both kwargs `None`) → byte-identical legacy flow (existing tests green unmodified — INV-1).
- Integration: auto row visible to `/status` `last_run` (§4.3).

**Depends on:** T4 (repo type), T1 (summary). **Blocks:** T5.

---

### T5 — `MaintenanceApiService` (manual orchestration) [AM-2, AM-9, AM-10, AM-11, AM-12, AM-15]

**Files:** NEW `daemon/services/maintenance_api_service.py`, `daemon/services/maintenance.py` (`is_idle` wrapper), `daemon/manager.py` (wiring), `daemon/constants.py` / `daemon/config.py` (T7 inputs)

6.1 **Error carrier** (service-level, FastAPI-free so the service unit-tests without HTTP):

```python
@dataclass
class MaintenanceError(Exception):
    code: str; http_status: int; message: str; details: dict[str, Any] = field(default_factory=dict)
```

The router maps it 1:1 to `HTTPException(status, detail={"error": code, "message": message, **details})` — the structured-dict shape (plane.py pattern; A-8 RATIFIED, binding for all 5 endpoints).

6.2 **Ctor:**

```python
class MaintenanceApiService:
    def __init__(
        self,
        config: PersistenceConfig,
        checkpointer: CheckpointerAdapter,
        cleanup_job: CheckpointCleanupJob,          # manual entry point (T1.7, AM-2 ordering)
        runs_repo: MaintenanceRunsRepository,
        run_lock: MaintenanceRunLock,               # SAME instance the auto cycle holds
        maintenance_service: MaintenanceService | None = None,  # idle advisory probe
        fresh_seconds: int = MAINTENANCE_DRY_RUN_FRESH_SECONDS, # 300 [§6.4 CONFIRMED]
    ): ...
```

`manager.initialize()` builds it right after the job registration (~`manager.py:2631`), stores as `self._maintenance_api_service`. The checkpointer reference is passed explicitly (same object the job received, `manager.py:2616`) so `availability()` needs no private reach-through.

6.3 **Boot reconciliation — moved to the lifespan boot sweep (T8, AM-7).** The service no longer sweeps at construction; the sweep is an unconditional lifespan-start step (T8) that runs before any endpoint can observe a phantom `running` row. (Supersedes the prior draft's constructor-time reconciliation — same recovery, earlier + unconditional, per AM-7.)

6.4 **Methods** (all async; repo calls via `asyncio.to_thread`):

- **`availability() -> dict`** — returns the Contract-v2 state enum [AM-13]: `state ∈ {ready, backend_unsupported, subsystem_disabled, kill_switched}`; `eligible` is **derived** (`state === 'ready'`); `backend = "postgres" | "sqlite"`; `reason` is a diagnostic string, NOT for FE branching. Backend gate: `isinstance(checkpointer, PostgresCheckpointerAdapter)` (same gate as `checkpoint_prune.py:123`). `kill_switched` rendering (200, not 503) when `MAINTENANCE_ENDPOINTS_ENABLED=0` — the kill-switch gate for #2–#5 lives in the router (T6.2); `/availability` itself is NEVER 503'd by the kill-switch [AM-13]. The transient startup race keeps the frozen 503 `not_initialized` (service not yet wired) — the router's not-initialized path carries `state: "subsystem_disabled"` semantics per the overview §1 notes.
- **`status() -> dict`** — `config` block is read from the **LIVE effective env at request time** (call-time env read, precedent `checkpoint_prune.py:75-84`; NOT boot-cached config) [R-6, leader ruling (a)]: `checkpoint_max_per_thread` (PersistenceConfig), `checkpoint_max_per_thread_floor` (constant, T7), `cleanup_interval_hours` (`checkpoint_cleanup_interval`), `blob_prune_dry_run_env_default` (`"1" if CHECKPOINT_BLOB_PRUNE_DRY_RUN else "0"`), `blob_prune_destructive_armed` (`blob_prune_destructive_enabled()` — the effective dual-arm state, also read per-request [R-6]); `last_run` = `latest_completed_for_section("checkpoint-cleanup")` rendered as `{run_id, kind, started_at, completed_at, status, summary}` (`null` when none) — **`last_run` semantics per [AM-9]: latest `succeeded|failed` of `kind ∈ {auto, manual_execute}`; `manual_dry_run` NEVER surfaces** (repo kinds default enforces it); `in_flight` = **any `running` row** [AM-9] via `get_running()` (the partial index guarantees at most one), rendered `{run_id, kind, started_at, triggered_by}` or `null`. Timestamps are already TEXT ISO strings (INV-7) — serialized verbatim (`+00:00` suffix, A-7 CONFIRMED).
- **`dry_run(requester: RequesterInfo) -> dict`** — **takes the SAME single-flight gate [AM-4]: it is a `kind=manual_dry_run` run row; its scan needs a stable blob set for `expected_bytes` to mean anything.** Sequence: lock acquire → conditional INSERT (`manual_dry_run`, `requester_json` stamped) → conflict → 409 `run_in_flight` naming the in-flight run → `result = cleanup_job.run_checkpoint_prunes(destructive=False)` → mark row `succeeded` + `summary_json` = full dry-run payload → release in `finally`. Responds the Contract-v2 §3 shape: `{run_id, would_delete: {checkpoint_rows, writes, blobs, bytes}, would_delete_count, would_free_bytes, scanned: {thread_ns_pairs}, skipped, duration_ms, fresh_until}` where `would_delete_count`/`would_free_bytes` are the canonical AM-11 fields, `fresh_until = now_utc_iso(now + fresh_seconds)`, `skipped` from `blobs.skipped` capped at 1000 + `skipped_truncated` [AM-10]. No deadlock: non-blocking acquire, no nesting, dry-run is terminal before execute references it.
- **`execute(payload: CheckpointCleanupExecuteRequest, requester: RequesterInfo) -> dict`** — validation chain in the Contract-v2 order, first failure raises (router-level checks come FIRST — see T6.2 ordering: **Origin guard, then kill-switch, then these service gates**):
  1. 503 `backend_unsupported` if not `isinstance(checkpointer, PostgresCheckpointerAdapter)` (SQLite can't run this at all);
  2. `payload.confirm is True` else 400 `confirm_required`;
  3. `payload.dry_run_run_id` present else 400 `dry_run_required` (field is Optional in the schema; 422 is never emitted for it);
  4. `runs_repo.get_dry_run(id)` exists else 404 `not_found` (`details: {run_id}`);
  5. age = `(now_utc_naive() - naive_of(row.started_at)).total_seconds()` ≤ `fresh_seconds` else 400 `dry_run_stale` (`details: {age_seconds, max_age_seconds}`) — no clock monkeypatching needed in tests: seed an old row directly; (naive-vs-naive comparison, R-5);
  6. `payload.expected_bytes == row.summary_json["would_delete"]["bytes"]` else 400 `byte_count_mismatch` (`details: {expected, stored}`) — `row` is the dry-run row from step 4, so its payload lives in `summary_json` (it becomes `dry_run_summary_json` when stamped onto the execute row below); **the ONLY scope pin [AM-3]; `skipped[]` is informational and NOT part of the confirm echo; pair-count/skip-hash pins were REJECTED** (they false-refuse in a live daemon and defeat the freshness window);
  7. **single-flight gate [AM-4 + AM-5]:** lock acquire → conditional INSERT (`manual_execute` row, `status='running'`, `triggered_by='user'`); INSERT conflict → 409 `run_in_flight` carrying `details.run_id` + `details.started_at` of the IN-FLIGHT run (read via `get_running()` — not the caller's; nested under `details` per [C-2, v3 fix pass]), **NO row written**; a refused acquire logs ONE INFO line with requester forensics [AM-6].
  Then: stamp the decision-input audit fields on the run row [AM-15]: `dry_run_run_id`, `expected_bytes`, `dry_run_summary_json` = **full dry-run snapshot incl. `skipped[]`** (survives any future dry-run-row pruning), `confirm`, `advisory`, `env_flags_json = {blob_prune_dry_run, blob_prune_destructive, destructive_override: true}` (proves INV-2 — the override kwarg, not env, armed the DELETE), `requester_json = {peer_ip, user_agent, origin}` (forensics, never attribution; no auth exists). Compute `advisory = None if await idle_probe() else "system_busy"` (advisory, never a refusal), compute `expected_duration_ms_hint` [AM-12/A-11 RATIFIED] = the referenced dry-run's `duration_ms` (**unit: milliseconds** [R-5, v3 fix pass] — the old "ceil-seconds" wording was wrong; canonical unit definition lives once in plan-overview §4, FE derives its own display copy), spawn `self._executing_task = asyncio.create_task(self._execute_run(run_id))` kept in a `set` with done-callback discard (GC-safe; memory O(1) — §6-R5), respond 202-shaped `{run_id, status: "running", started_at, advisory, expected_duration_ms_hint}` [AM-12 — both fields always present].
  `_execute_run`: `result = cleanup_job.run_checkpoint_prunes(destructive=True)` (**Op E → Op D, AM-2**) → row `succeeded` + `summary_json`; any exception → row `failed` + `error_json={"code": "execution_error", ...}` (**infra faults only — pair failures live in `summary.skipped`**, Focus Area 3 state machine); `finally: run_lock.release()`.
- **`get_run(run_id) -> dict`** — repo `get` → 404 `not_found` (`details: {run_id}`) or `{run_id, kind, status, started_at, completed_at, summary, error}` (poll target for Phase 2). **State machine [AM-6]:** `running` → `{status, completed_at: null, summary: null}`; terminal (`succeeded|failed|interrupted`) → full body; unknown → 404.
- **idle probe:** `MaintenanceService` gains `async def is_idle(self) -> bool: return await self._is_idle()` (thin public wrapper over `maintenance.py:258`; `_is_idle` docblock's known blind-spots are exactly why this is advisory). `maintenance_service=None` → probe returns `True` (no advisory) so unit tests don't need one.

6.5 **Shutdown hook:** manager shutdown tuple (~`manager.py:11564`) gains `shutdown_maintenance_api_service` — cancel+await `_executing_task` if alive, mark its row `failed/run_interrupted_by_shutdown`... **correction per [AM-6/AM-7] state machine: shutdown marks the row per the 4-state enum — `failed` with `error_json.code="run_interrupted_by_shutdown"`** (a graceful shutdown is not a crash; the boot sweep's `interrupted` state is reserved for restart-orphaned rows). Best-effort, never raises.

**Acceptance:** unit suite (§4.2) covers the full validation chain in order, advisory + hint behavior, row lifecycle with decision-input audit fields, gate interplay incl. conflict semantics; every stable code renders the exact frozen body keys.

**Depends on:** T1, T2, T2b, T3, T4, T7. **Blocks:** T6.

---

### T6 — Router, schemas, registration + Origin guard + kill-switch mapping [AM-1, AM-13]

**Files:** NEW `daemon/routers/maintenance.py`, NEW `daemon/routers/maintenance_origin_guard.py`, `daemon/routers/schemas.py` (append), `daemon/api.py`

7.1 **Schemas** (`daemon/routers/schemas.py`, appended after the Plane block ~`:1388`, mirroring `PlaneProjectSync*` style — `Field(..., description=...)` + `json_schema_extra` examples): `CheckpointCleanupAvailabilityResponse` (`eligible`, `backend`, `state`, `reason` — state enum per AM-13), `CheckpointCleanupStatusResponse` (+ nested `CheckpointCleanupConfigBlock`, `CheckpointCleanupLastRun`, `CheckpointCleanupInFlight`), `CheckpointCleanupDryRunResponse` (incl. `would_delete_count`, `would_free_bytes`, `skipped`, `skipped_truncated` [AM-10/AM-11]), `CheckpointCleanupExecuteRequest` (`dry_run_run_id: str | None = None`, `expected_bytes: int | None = None`, `confirm: bool = False` — Optional fields so the router emits the frozen 400s, never FastAPI's bare 422; **NO `idempotency_key` field — removed per [AM-17]; 409-adoption replaces it**), `CheckpointCleanupExecuteResponse` (`advisory: str | None`, `expected_duration_ms_hint: int` [AM-12]), `CheckpointCleanupRunResponse` (+ `skipped` in summaries), `CheckpointCleanupErrorResponse` (`error`, `message`, `details: dict | None`). Every `mode="before"` validator (if any) starts with an isinstance guard (MCP blueprint rule — raw AttributeError escapes ValidationError → 500 instead of 422).

7.2 **Origin guard** (`daemon/routers/maintenance_origin_guard.py`) [AM-1 — INV-10]: a single `require_trusted_origin(request: Request) -> None` FastAPI dependency, applied FIRST on `/status`, `/dry-run`, `/execute`, `/runs/{run_id}`; **`/availability` is EXEMPT** (the FE gear probe must see disabled state cleanly; it is non-destructive). Rule order (fail-closed at the end):

1. **No `Origin` header → allow** (curl, systemd, programmatic operators).
2. **`Origin` matches the daemon's own external origin (same-origin) → allow.** Browsers attach `Origin` even on same-origin POSTs; deny-all would 403 the daemon-served SPA's own execute — the shipped product's primary flow (production is monolithic: daemon serves the FE dist via the SPA catch-all, `api.py:2816-2828`). Zero-config production must work. **Same-origin derivation [R-7]:** the daemon derives its own external origin from the request `Host` + scheme at request time; `X-Forwarded-*` headers are untrusted and NOT consulted.
3. **`Origin` host ∈ localhost-family (`localhost`, `127.0.0.1`, `[::1]`, any port, http/https) → allow.** Zero-config dev: FE dev server on `:4199` proxies to `:8079`; the daemon sees `Origin: http://localhost:4199`. (Leader ruling 1: default empty `MAINTENANCE_TRUSTED_ORIGINS` + localhost auto-trust ACCEPTED; residual local-malicious-http-page risk = machine-level compromise, deferred to the global-posture ticket.)
4. **`Origin ∈ MAINTENANCE_TRUSTED_ORIGINS`** (CSV env, default empty) **→ allow** (explicit opt-in for LAN-browser origins).
5. **Anything else (incl. `Origin: null` from sandboxed iframes / `file://`) → 403 `origin_not_trusted`.**

This guard is the **sole browser-borne defense layer** in v1 — under CORS `allow_origins=["*"]` + `allow_credentials=True` (`api.py:2613-2619`), non-credentialed cross-origin fetches can read response bodies (verified against installed Starlette `middleware/cors.py`), so an http-origin page could otherwise run dry-run → read `run_id`+`expected_bytes` → execute with valid echoes. It does NOT replace the 6-gate mistake/staleness model — complementary. One guard function, source-grep pinnable. **[R-21, v3 fix pass]** every 403 refusal logs ONE **INFO** line (origin, peer_ip, request path) — forensics for near-miss/suspect-origin requests without spamming WARNING/ERROR lanes.

7.3 **Router** (`daemon/routers/maintenance.py`):

```python
router = APIRouter(prefix="/maintenance/checkpoint-cleanup", tags=["maintenance"])
```

- **Gate ordering (each endpoint's dependency chain): [INV-10/AM-1] `require_trusted_origin` FIRST → [AM-13] kill-switch check → `get_maintenance_api_service` (not_initialized) → service gates.** A request failing the Origin guard gets `403 origin_not_trusted` without inspecting any other gate.
- `get_maintenance_api_service(request: Request) -> MaintenanceApiService` — reads `request.app.state.maintenance_api_service`; `None` → `HTTPException(503, detail={"error": "not_initialized", "message": ..., "state": "subsystem_disabled"})` (503 not 500/422, per contract; structured body per plane.py:71-76 precedent).
- **Kill-switch mapping [AM-13]:** `MAINTENANCE_ENDPOINTS_ENABLED=0` (boot-read) → endpoints #2 (`/status`), #3 (`/dry-run`), #4 (`/execute`), #5 (`/runs/{id}`) → **503 `{"error": "maintenance_disabled", ...}`**; **`/availability` → 200 with `{eligible: false, state: "kill_switched", reason: "MAINTENANCE_ENDPOINTS_ENABLED=0"}`** (FE hides the menu entry cleanly). One switch — the §6.3 `MAINTENANCE_SERVICE_DISABLED` variant is DROPPED (YAGNI). INV-1 preserved: the kill-switch gates the **API surface only**; the auto-cycle env dual-arm is untouched — auto stays destructive-if-armed while the manual API refuses (correct by design; runbook documents it).
- Five endpoints: `GET /availability` (200), `GET /status` (200), `POST /dry-run` (200), `POST /execute` (202), `GET /runs/{run_id}` (200/404). Each carries `responses={...}` OpenAPI docs mapping every frozen status code to `CheckpointCleanupErrorResponse`/description (plane.py:85-94 shape). Bodies delegate 1:1 to `MaintenanceApiService`; `MaintenanceError` → `HTTPException(e.http_status, detail={"error": e.code, "message": e.message, **e.details})`.
- **Unexpected-exception catch-all [A-8; documented [CF-6 doc-repair, v3.1] — code already shipped]:** `_call_service` wraps every maintenance handler; any exception that is NOT a `MaintenanceError` (i.e. an unhandled bug or infra fault) → **500** with the contract-shaped body `{"error": "internal_error", "message": <str>, "details": {}}` (structured dict per A-8/plane.py pattern; `details` empty). This is a documentation catch-up of shipped behavior — no new wire surface, no change to the 4xx gate chain.
- **No auth** (posture match — AM-1's guard + AM-13's switch are the v1 defense; global posture deferred ticket); **no cancel endpoint** (leader ruling 3 — v2 insertion point documented at T3); no SSE; no request body on dry-run (`body: None` reserved, plane.py:98 pattern).

7.4 **Registration** (`daemon/api.py`): import `maintenance_router`; `api_router.include_router(maintenance_router)` inside the `:2687-2726` block (e.g. after `recovery_router`) — `api_router` is mounted via `app.include_router(api_router)` at `:2726`, which precedes the SPA catch-all, so first-match routing reaches us (research §2.1). Then `app.state.maintenance_api_service = manager._maintenance_api_service` immediately after the `MigrationWorker` wiring (~`api.py:423`; both require `manager.initialize()` to have run at `:388`).

**Acceptance:** OpenAPI docs render all five operations with documented error models; a request to any endpoint on an app without the state wired returns 503 `not_initialized` (not 500, not the SPA index.html); Origin guard ordering pin — untrusted Origin + kill-switch OFF + confirm missing → body is `origin_not_trusted` (first check wins).

**Depends on:** T5, T7. **Blocks:** T9 (integration suite drives these endpoints).

---

### T7 — Constants + config [AM-1, AM-13]

**Files:** `daemon/constants.py`, `daemon/config.py`

- `MAINTENANCE_DRY_RUN_FRESH_SECONDS: int = 300` (§6.4 CONFIRMED; consumed by T5.2/T5.4).
- `MAINTENANCE_ENDPOINTS_ENABLED: bool = True` (default `1` [AM-13]; **boot-read env — read once at startup, flipping requires a restart** (bounded by Phase 3 activation anyway); consumed by the T6.3 router gate. Boot logs ONE INFO line when OFF (PlaneSyncWatchdog no-key precedent) [AM-13]).
- `MAINTENANCE_TRUSTED_ORIGINS: str = ""` — CSV, **default empty** (leader ruling 1 ACCEPTED; localhost-family auto-trust needs no env in dev or prod) [AM-1]; parsed once at boot into the guard's allow-set.
- `CHECKPOINT_MAX_PER_THREAD_FLOOR: int = 1`; `config.py:611-624` `ge=1` → `ge=CHECKPOINT_MAX_PER_THREAD_FLOOR` so the `/status` `config.checkpoint_max_per_thread_floor` field cannot drift from the pydantic constraint.

No env-var reader changes beyond reading these constants at boot. **Acceptance:** unit test that `MAINTENANCE_ENDPOINTS_ENABLED=0` 503s endpoints #2–#5 and renders `/availability` 200 `kill_switched` (monkeypatched at boot in the unit suite); flipping it back restores; boot INFO line asserted when OFF.

---

### T8 — Boot sweep at lifespan start [AM-7]

**Files:** NEW `daemon/services/maintenance_boot_sweep.py`, `daemon/api.py`

8.1 **`sweep_interrupted_running_runs(runs_repo, section="checkpoint-cleanup") -> int`** — at lifespan start (inside the `@asynccontextmanager` lifespan, `api.py:202`, AFTER `manager.initialize()` provides the engine/repo, BEFORE the app serves): **unconditional rowcount-guarded CAS `running → interrupted`** — `cas_running_to_interrupted(section)` (T4.2), the PlaneSync `fail_stale_syncing` crash-wedge pattern (`plane_sync_watchdog_service.py:299-316`). **No age gate** — a boot-time `running` row is an orphan by definition under the single-daemon assumption; an age gate would orphan young rows. Sets `status='interrupted'`, `error_json={"code": "run_interrupted", ...}`, `completed_at` = sweep time (TEXT ISO). **One summary log line** (`"maintenance boot sweep: N interrupted run(s)"`). **Boot-sweep-only — no live stale-running watchdog in v1** (unlike Plane, no live foreign process can own the row) [AM-7]. Resume is rejected by design: prune is retention-idempotent (re-run converges; per-pair independence means a partial pass finishes next run).

**Acceptance:** integration case 61 — seed a phantom `running` row (any age, incl. seconds old), boot the app fixture → row is `interrupted` with `error.code="run_interrupted"`, sweep summary logged once, `/status` shows no phantom `in_flight`.

**Depends on:** T4. **Blocks:** T9.

---

### T9 — Integration suite + wiring pin (was T8) 

**Files:** `tests/integration/test_maintenance_checkpoint_cleanup_api.py`, `tests/integration/test_checkpoint_cleanup_job_wiring_pin.py` — full enumeration in §4.3/§4.4; re-freeze item 3's four cases + AM-16 additions live here and in §4.1/§4.2.

**Depends on:** T1–T8. **Blocks:** nothing (terminal).

---

## 3. Commit / PR Slicing (suggested) — **blocking AMs first**

| Slice | Contents | Gate to merge |
|---|---|---|
| **PR-1** `feat(maintenance): checkpoint cleanup summaries + manual destructive override + AM-2 ordering` [R-14: close-out census MUST include the whole-tree grep for the 4 new suites — `test_checkpoint_prune_destructive_override.py`, `test_maintenance_checkpoint_cleanup_service.py`, `test_maintenance_run_lock_and_capture.py`, `test_maintenance_checkpoint_cleanup_api.py` — plus the ordering pin 9a; see DoD item 3] | T1 + T2 + T2b — **carries the AM-2 BLOCKER**: the manual entry point's Op E → Op D composition + the ordering pin + regression case 57 (excess-rows execute must NOT mismatch) land in the earliest slice | Auto-behavior-unchanged pins green; AST dominance pin green; ordering pin green; existing suites (`test_maintenance_prune_direct_anti_join.py`, `checkpoint_prune_real_saver.py` when PG available) green. No API surface yet — safe, revertible. |
| **PR-2** `feat(maintenance): single-flight gate + maintenance_runs audit persistence (FINAL schema) + boot sweep` [R-14: close-out census includes the same 4-suite whole-tree grep + the wiring-pin extension 55] | T3 + T4 + T8 (+§4.2 gate/repo unit suites, §4.3 auto-row + contention integration cases, wiring-pin extension) — carries AM-4/5/6/7/15: partial unique index claim, 409-no-row, refusal-row deletion, 4-state machine, dual-dialect index render (case 64) | Disposable-PG + file-SQLite suites green; dual-driver partial-index render proven (fallback documented if not); INV-1 unwired-job pins green. |
| **PR-3** `feat(maintenance): checkpoint-cleanup API surface (Contract v3) + origin guard + kill-switch` | T5 + T6 + T7 (+§4.2 service suite, §4.3 full API matrix) — **carries the AM-1 BLOCKER** (`require_trusted_origin`, INV-10) + AM-13 kill-switch + AM-9/AM-12 wire shapes. Note: AM-1 cannot land earlier than the endpoints it guards — do NOT defer PR-3 behind PR-2 review latency; the browser-borne defense ships with the surface or not at all. | Full execute-gate matrix green on disposable PG (Origin-first ordering); Origin guard matrix green; kill-switch behavior green; OpenAPI docs render; `uv run python -m pytest` default partition unaffected (integration/postgres markers exclude by default per `pyproject.toml:80`). |

PR-1 is the riskiest diff (touches the shipped prune + carries the AM-2 blocking fix) and lands alone; PR-2/PR-3 could fold together if review bandwidth allows, keeping their test files separate — but PR-3 (AM-1) must not trail by more than one review cycle.

---

## 4. Test Plan (full enumeration)

**Total: 67 enumerated BE cases** [R-1 v3 recount + case 67 added [CF-6 doc-repair, v3.1]; supersedes any 64/65 figure elsewhere]: §4.1 = 10 (incl. 9a) · §4.2 = 29 (incl. 11a, 17a) · §4.3 = 27 (numbered 37–67, with 53 retired and replaced by 64; 67 = A-8 catch-all shape pin) · §4.4 wiring pin = 1.

**Runner conventions (all suites):** `uv run python -m pytest` from the worktree root (bare `pytest` = broken Homebrew PATH — execution gate). Unit files run under default addopts. Integration file: `uv run python -m pytest tests/integration/test_maintenance_checkpoint_cleanup_api.py --override-ini="addopts=" -m "integration and postgres"` (serial; disposable per-test DBs make it xdist-SAFE, but serial matches the tests/postgres convention and keeps PG load sane). Markers `integration` + `postgres` exist (`pyproject.toml:75-80`). PG creds via `PG_TEST_*` env only (`tests/helpers/checkpoint_prune_pg.py:32-36`); fixtures scrub `ENSEMBLE_DB_DSN` and assert no DSN contains `ensemble_prod`; loud skip via `require_postgres()` when PG is down — never a mock substitution. **NEVER fresh SQLite for the checkpoint stack** (migration `20260714_000001` is PG-only) — the API-suite app fixture builds the checkpoint side against the disposable-PG stack only. The `maintenance_runs` audit table itself is create_all-built on both drivers [AM-15], so the unit suite's file-backed SQLite repo engine is legitimate (create_all from the model only; no migrations involved). Env ladder fixture (`_default_ladder` pattern, `checkpoint_prune_real_saver.py:94` [R-3 cite fix — was `:105-110`]): every test starts with `CHECKPOINT_BLOB_PRUNE_DRY_RUN`/`_DESTRUCTIVE` deleted; arming is per-test explicit.

### 4.1 `tests/unit/services/test_checkpoint_prune_destructive_override.py` (mock adapter, `_pg_adapter_mock` shape from `test_maintenance_prune_direct_anti_join.py:46`)

1. `test_default_none_uses_env_gate_off` — no kwarg, env off → dry-run, `count_blobs_anti_join` awaited, delete sentinel untouched.
2. `test_default_none_uses_env_dual_arm` — BOTH flags armed, no kwarg → delete arm reached (env path intact for auto).
3. `test_env_single_arm_stays_dry_run` — `DESTRUCTIVE=1` alone, no kwarg → dry-run (dual-arm not loosened; INV-1).
4. `test_destructive_true_reaches_delete_with_env_off` — kwarg True, env absent → delete awaited once; INV-2 proof.
5. `test_destructive_false_overrides_armed_env` — kwarg False + both flags armed → dry-run.
6. `test_destructive_true_preserves_zero_refs_fail_safe` — kwarg True + refs=0 staged → ERROR log + skip, delete sentinel untouched (INV-3).
7. `test_destructive_true_preserves_max_refs_skip` — kwarg True + refs over cap → `MAX_REFS_EXCEEDED` skip.
8. `test_ast_dominance_pin_still_green` — thin re-run/parameterization guard: the dominance pin passes against the modified module (the real assertion lives in the existing file; this documents intent in close-out).
9. `test_dry_run_accumulation_fields_populated` — (with T1.1) dry-run sweep fills `would_delete_count/would_free_bytes` [AM-11 canonical names]; destructive sweep leaves them 0 and fills `total_deleted/total_bytes_freed`.
9a. `test_manual_entry_point_orders_blobs_before_rows` — [AM-2] call-order pin: `run_checkpoint_prunes(destructive=True)` awaits the blob arm before the row arm (mock job records call sequence); auto `execute()` unaffected (its inline D→E order is pinned by existing suite 46/47).

### 4.2 `tests/unit/services/test_maintenance_checkpoint_cleanup_service.py` + `test_maintenance_run_lock_and_capture.py`

Lock & capture:
10. `test_lock_fail_fast_returns_false_when_held` — `asyncio.wait_for(acquire(...), 0.1)` → False, not timeout (non-blocking proven). **[AM-4] the lock is step 1 only — the DB claim is asserted separately (case 11a).**
11. `test_lock_in_flight_reports_holder_context` — run_id/kind/started_at of the holder visible; None after release.
11a. `test_conditional_insert_conflict_yields_409_no_row` — [AM-5] seed a `running` row; second `insert` returns conflict → 409 body names the IN-FLIGHT run's `run_id`/`started_at`; **NO row written** for the refused caller; ONE INFO log line with requester forensics [AM-6].
12. `test_run_id_format_matches_contract` — [AM-8] regex pin `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$` (colon-free, URL-clean); collision retry regenerates the hex8 suffix only.
13. `test_execute_unwired_job_legacy_behavior` — job without lock/repo kwargs: existing ops run, no rows (INV-1).
14. `test_execute_auto_row_lifecycle` — wired job: row `auto/running` → `succeeded` with summary; `triggered_by='system'`; `env_flags_json` stamped.
15. `test_execute_auto_skips_when_gate_held` — [AM-4/Focus Area 6, rewritten from lock-only semantics] external holder (lock + running row) → no ops, **NO row** (AM-6), DEBUG log carrying the in-flight run_id, `job.last_run` updated (non-raising skip — re-arms the interval).
16. `test_prune_per_thread_returns_summary` — mock adapter, destructive Op D: counters match delete returns; `excess_pairs/scanned_pairs` populated.
17. `test_run_checkpoint_prunes_dry_run_has_zero_deletes` — `destructive=False`: `delete_*`/`delete_writes_excluding` sentinels untouched; `would_delete_*` populated via `count_writes_excluding`.
17a. `test_summary_skipped_cap_and_truncation_flag` — [AM-10/AM-15] >1000 skipped pairs staged (mock) → `skipped` truncated to 1000 entries + `skipped_truncated: true` in the summary dict.

Service (`MaintenanceApiService` with real repo on a file-backed SQLite engine — repo is backend-agnostic; the CHECKPOINTER stays a mock. NOTE: this is not a "fresh SQLite boot" — no migrations involved, `create_all` from the model only [AM-15 makes this both-driver-legitimate]; the PG-only constraint concerns the checkpoint-blob prune, not this audit table):
18. `test_availability_state_enum` — [AM-13, updated] mock isinstance-flip: `{eligible: true, backend: postgres, state: "ready"}` vs `{eligible: false, backend: sqlite, state: "backend_unsupported", reason: blob_prune_postgres_only}`; `eligible` derived from `state === "ready"`.
19. `test_status_empty_and_with_prior_run` — `last_run: null`; after seeding a terminal row: shape pins (config keys, summary passthrough incl. dual-flavor `blobs` keys + `skipped` [AM-10/AM-11], in_flight null).
20. `test_status_in_flight_reflects_running_row` — [AM-9, updated] seed a `running` row (any kind) → `in_flight` populated from the DB row `{run_id, kind, started_at, triggered_by}`; terminal-only rows → `in_flight: null`.
21. `test_execute_gate_missing_confirm` → 400 `confirm_required`.
22. `test_execute_gate_missing_dry_run_id` → 400 `dry_run_required`.
23. `test_execute_gate_unknown_dry_run` → 404 `not_found` + `details.run_id`.
24. `test_execute_gate_stale_dry_run` — seed dry-run row `started_at = now - 400s` (TEXT ISO) → 400 `dry_run_stale` with `age_seconds > 300, max_age_seconds = 300`.
25. `test_execute_gate_byte_mismatch` → 400 `byte_count_mismatch` with `{expected, stored}` from the stored dry-run row.
26. `test_execute_gate_run_in_flight` — [AM-5, rewritten from lock-only] seed a `running` row (or hold lock+row via a blocked job) → 409 body carries the HOLDER's (in-flight) `details.run_id`/`details.started_at` (nested under `details` [C-2, v3 fix pass]) — the 409-adoption contract payload [AM-17]; NO row for the refused caller.
27. `test_execute_gate_backend_unsupported` — sqlite-shaped checkpointer → 503 `backend_unsupported` (before any other service gate).
28. `test_gate_order_first_failure_wins` — [updated for Contract v3 ordering] router-level: untrusted Origin + kill-switch OFF + confirm missing → body is `origin_not_trusted` (INV-10: Origin FIRST); kill-switch OFF + confirm missing (Origin clean) → `maintenance_disabled` (kill-switch SECOND); service-level: confirm missing AND stale dry-run AND held gate simultaneously → `confirm_required` (frozen order).
29. `test_execute_happy_path_mock` — full chain with mock job → 202 shape `{run_id, status: "running", started_at, advisory: null, expected_duration_ms_hint}` [AM-12 — both fields present]; row `manual_execute/running` with decision-input audit fields (`dry_run_run_id`, `expected_bytes`, `dry_run_summary_json` incl. `skipped[]`, `confirm`, `advisory`, `env_flags_json`, `requester_json`) [AM-15] → awaited `run_checkpoint_prunes(destructive=True)` → row `succeeded` + summary; gate released.
30. `test_execute_marks_failed_row_on_exception` — job raises → row `failed` + `error_json.code='execution_error'` (infra faults only); gate released (finally).
31. `test_execute_advisory_and_hint` — [AM-12] idle probe False → 202 carries `advisory: "system_busy"` + `expected_duration_ms_hint` == the referenced dry-run's `duration_ms` (**unit: ms** [R-5, v3 fix pass] — assert raw equality with the ms value, no ceil-seconds conversion); probe True → `advisory: null` (still present).
32. `test_dry_run_409_when_in_flight` + `test_dry_run_happy_shape` — [AM-4] dry-run takes the SAME gate (its own `manual_dry_run` running row conflicts); happy shape pins: `would_delete`/`would_delete_count`/`would_free_bytes`/`scanned`/`skipped`/`duration_ms`/`fresh_until` [AM-10/AM-11].
33. `test_dry_run_row_written` — every dry-run persists `manual_dry_run` row with the response payload as summary (audit invariant; becomes the `dry_run_summary_json` source for execute).
34. `test_error_body_shape_pins` — for each stable code (incl. `maintenance_disabled` + `origin_not_trusted` [AM-1/AM-13]): `{"error", "message"}` keys present, `details` keys exactly as contracted.
35. `test_boot_sweep_marks_interrupted` — [AM-7, rewritten] seed `running` row (any age — no age gate), run the sweep → row `interrupted` + `error_json.code="run_interrupted"` + `completed_at` = sweep time (TEXT ISO); one summary log line; terminal `succeeded`/`failed` rows untouched.
36. `test_kill_switch_disables_api_surface` — [AM-13, updated] `MAINTENANCE_ENDPOINTS_ENABLED=0` → service/router gate 503s #2–#5 with `maintenance_disabled`; **`/availability` returns 200 `{eligible: false, state: "kill_switched"}`**; boot-read semantics pinned (flag read at startup, not per-request).

### 4.3 `tests/integration/test_maintenance_checkpoint_cleanup_api.py` (disposable PG)

Harness: mirror `checkpoint_prune_real_saver.py` — `evict_langgraph_mocks` autouse fixture, `create_disposable_db`/`drop_database` per test, `real_pg_checkpointer(name, dsn)` production-shaped stack, staging helpers copied from that file (aput chains for blobs; drift staged for zero-refs). HTTP layer: minimal `FastAPI()` app + `include_router(maintenance_router)` + `app.state.maintenance_api_service = <real service>` + `httpx.AsyncClient(transport=ASGITransport(app))` (pattern: `test_vscode_security_integration.py:206-211`). Repo engine: `create_engine(disposable_dsn)` + `SQLModel.metadata.create_all` for `maintenance_runs` on the SAME disposable DB (single DB per test — no shared state); the same create_all path also builds the table on the unit suite's file SQLite [AM-15 — both-driver build is itself under test, case 64].

37. `test_availability_endpoint_pg` — 200 `{eligible: true, backend: postgres, state: "ready"}` [AM-13].
38. `test_availability_endpoint_sqlite` — service built on a SQLite-shaped checkpointer mock → `{eligible: false, state: "backend_unsupported", reason: blob_prune_postgres_only}`; menu-probe contract honored (FE branches on `state`, not `reason`).
39. `test_503_not_initialized_all_endpoints` — app WITHOUT `app.state.maintenance_api_service` → every route 503 `not_initialized` (structured body, not the SPA fallback).
40. `test_dry_run_counts_derive_from_excess_enumeration` — stage pair A with 6 checkpoints (keep-N=3), pair B with 2 → `would_delete.checkpoint_rows == 3`, `writes == count_writes_excluding` parity, `scanned.thread_ns_pairs == 2`; verified against direct SQL on the disposable DB.
41. `test_dry_run_bytes_from_anti_join_count_arm` — stage unreferenced blobs (superseded delta chains) → `would_delete.blobs/bytes` + `would_delete_count/would_free_bytes` [AM-11] match a direct `count_blobs_anti_join`-equivalent SQL sum; `delete_blobs_anti_join` provably NOT called (row counts unchanged).
42. `test_dry_run_freshness_window` — `fresh_until == started_at + 300s` (±2s tolerance, TEXT ISO `+00:00` strings); row `manual_dry_run` persisted.
43. `test_dry_run_surfaces_zero_refs_skips` — **[re-freeze item 3 case (a) / AM-16a]** stage a drifted pair → response `skipped` includes `{"reason": "ZERO_REFS_FAIL_SAFE"}` (exact literal pin, `checkpoint_prune.py:185-200`) and `would_delete` EXCLUDES that pair's blobs.
44. `test_execute_happy_path_disposable_pg` — dry-run → execute(confirm, expected_bytes) → 202 (with `advisory`/`expected_duration_ms_hint` [AM-12]) → poll `GET /runs/{id}` → `succeeded`; DB asserts: checkpoint rows reduced to keep-N, writes pruned, unreferenced blobs gone, freed bytes == dry-run estimate (AM-2 ordering exercised end-to-end), `maintenance_runs` row `succeeded` with full summary + decision-input audit fields [AM-15], `/status.last_run` populated `kind='manual_execute'`.
45. `test_execute_zero_refs_no_mass_delete` — destructive execute against drifted staging → zero-ref pair's blobs survive (INV-3 live proof).
46. `test_auto_cycle_unchanged_default_dry_run` — env scrubbed; wire the job (gate+repo) on the disposable stack; run `execute()` → `checkpoint_blobs` row count + bytes byte-identical; row `auto` written with `blobs.destructive=false` (INV-1 live proof).
47. `test_auto_env_dual_arm_unchanged` — BOTH flags armed; no-kwarg auto path → deletes happen (env path intact); WITH `destructive` kwarg absent from auto call sites (source pin: auto never passes the kwarg; auto order stays D→E — INV-9).
48. `test_overlap_manual_holds_auto_defers` — [AM-4/AM-6, rewritten] manual holds the gate (lock + running row) → auto `execute()` → no ops, NO row, DEBUG with in-flight run_id, `job.last_run` updated (non-raising skip path).
49. `test_overlap_auto_holds_manual_409` — [AM-5, rewritten] auto holds the gate (`kind='auto'` running row) → POST /execute → 409 body `run_id`/`started_at` reflect the auto holder (409-adoption payload [AM-17]).
50. `test_overlap_two_manuals_409` — [AM-5, rewritten] hold via a slow execute (large staged dataset or a blocked job mock; its `manual_execute` running row is the claim) → second POST /execute → 409 naming the first run; dry-run-while-execute → 409 as well (same single lane).
51. `test_audit_row_on_every_manual_run` — count rows by kind after a dry-run+execute sequence: ≥1 `manual_dry_run`, ≥1 `manual_execute`, all terminal; execute row carries `requester_json`/`env_flags_json`/`dry_run_summary_json` [AM-15].
52. `test_structured_error_body_shape_integration` — replay gates 21-27 over HTTP: exact body keys per code (catches router-level shape drift that service-level tests can't).
54. `test_runs_repo_ordering_and_filters` — seeded running/succeeded/failed/dry-run rows (TEXT ISO timestamps) → `latest_completed_for_section` returns the newest terminal non-dry-run row (NULL-handling + kinds filter proven — [AM-9] dry-run never wins `last_run`).

**New cases (re-freeze item 3 + AM-16 + AM-5 verification):**

56. `test_execute_succeeds_with_new_skip_pair_in_window` — **[re-freeze item 3 case (b) / AM-16b / AM-3]** dry-run → stage a NEW zero-refs/drifted pair → execute with the original `expected_bytes` → succeeds (bytes unaffected: skip pairs contribute 0 bytes to both runs; the bytes-only pin correctly ignores skip-set drift — deletion-conservative).
57. `test_execute_excess_rows_no_mismatch` — **[re-freeze item 3 case (c) / AM-2 BLOCKING regression pin / W-1 mechanism restated v3]** stage excess checkpoint rows (e.g. 6 checkpoints on one thread, keep-N=3, with blob-bearing superseded versions) → dry-run → execute → succeeds with byte counts matching the dry-run exactly. **Under the old D→E manual order this test FAILS BY SILENT OVER-DELETION, not by error:** every server check PASSES under D-first — the byte-equality gate (AM-3, check 5) compares the echo against the STORED dry-run row, so it always matches — and D unreferences blobs (`checkpoint_prune.py:12-17`) that the execute's blob pass then deletes beyond the confirmed echo. This regression case (with case 44's freed-bytes == dry-run-estimate assert) is the ONLY catcher; the byte-equality gate can never fire here (`decision-log.md` AM-2 rationale, corrected v3). Companion assertion: the manual entry point's call order is E-then-D (9a). **INV-13:** do NOT "fix" this class with a post-run actual-vs-expected completion gate.
58. `test_last_run_excludes_manual_dry_run` — **[re-freeze item 3 case (d) / AM-9]** seed `manual_dry_run` row NEWER than a terminal `manual_execute` row → `/status.last_run` shows the execute row (not the dry-run); dry-run queryable via `GET /runs/{dry_run_id}`; `in_flight` independent of `last_run`.
59. `test_origin_guard_matrix` — **[AM-1 / AM-16e — parametrized 6-way]** (i) no `Origin` header → allowed (200/202); (ii) same-origin (`Origin: http://localhost:8079`, the daemon's own external origin) → allowed; (iii) localhost-family — `http://localhost:4199` (FE dev server case), `http://127.0.0.1:9999`, `https://[::1]:8443` — allowed (any port, http/https); (iv) `MAINTENANCE_TRUSTED_ORIGINS` match (`http://ops-box.lan`) → allowed; (v) untrusted (`http://evil.example`) → **403 `origin_not_trusted`**; (vi) `Origin: null` (sandboxed iframe / `file://`) → 403. Applied on `/status`, `/dry-run`, `/execute`, `/runs/{id}`; `/availability` EXEMPT (reachable with untrusted Origin). **[Approver fold-in (c) 2026-09-27]** the unit half additionally parametrizes `MAINTENANCE_TRUSTED_ORIGINS` CSV edge cases — empty string / whitespace-only (no extras authorized), multi-entry CSV, empty entries between commas, malformed non-URL entries (**inert** — fail-closed, never literal-matched), case-folding, and near-miss origins (`http://ops-box.lan.evil`) — mirrored in `tests/unit/services/test_maintenance_checkpoint_cleanup_service.py::TestOriginGuardMatrix::test_trusted_origins_csv_edge_cases` so doc and code stay in sync.
60. `test_kill_switch_off_integration` — **[AM-13 / AM-16f]** boot app fixture with `MAINTENANCE_ENDPOINTS_ENABLED=0` → `/status`, `/dry-run`, `/execute`, `/runs/{id}` all 503 `maintenance_disabled`; `/availability` → **200 `{eligible: false, state: "kill_switched", reason: "MAINTENANCE_ENDPOINTS_ENABLED=0"}`**; boot INFO line logged once.
61. `test_boot_sweep_unconditional_cas` — **[AM-7 / AM-16g]** seed phantom `running` rows (one seconds-old — proving NO age gate), boot the app fixture (lifespan runs the sweep) → all flipped to `interrupted` + `error.code="run_interrupted"` + `completed_at` = sweep time; one summary log line; `/status` shows no phantom `in_flight`; no live watchdog exists (no background sweeper task).
62. `test_dual_arm_contention_409_and_auto_skip` — **[AM-16h / Focus Area 6]** both directions on one global lane: (i) auto running (running row) + manual execute arrives → 409 naming the auto run; (ii) manual running + auto tick fires → auto does NOT raise, skips ops, writes NO row, DEBUG-logs the in-flight run_id, updates `job.last_run` (the strictly sequential `_loop`, `maintenance.py:209-220`, guarantees no stacking and no loop death); (iii) dual-arm env armed during both — auto stays destructive-if-armed while manual 409s (INV-1 cell of the interplay matrix).
63. `test_409_body_run_id_adoption_contract` — **[AM-17 / AM-16i]** any 409 `run_in_flight` body carries `details.run_id` + `details.started_at` of the IN-FLIGHT run (verifiable against the seeded running row) — the payload the FE adopts to resume polling; assert `idempotency_key` appears NOWHERE in the execute contract (schema OpenAPI + request echo) [AM-17].
64. `test_partial_index_render_both_drivers` — **[AM-5 / AM-16j]** `SQLModel.metadata.create_all` on a disposable PG AND a file-backed SQLite → `inspect()` shows `uq_maintenance_runs_running_section` on both, unique, with the `status='running'` where-clause (PG: `postgresql_where` rendered; SQLite: partial index natively). **Scope [R-16, v3 fix pass]: the concurrent double-insert assertion runs on BOTH drivers** — two sessions, no app lock, seeded running row → concurrent double-insert → **exactly one row wins** on PG AND on file-SQLite (not PG-only). If the SQLite render fails: the documented fallback (plain conditional `INSERT … WHERE NOT EXISTS` + racy-belt caveat, T4.1) is asserted instead — fallback presence itself is pinned so the substitution is never silent. **[R-15, v3 fix pass]** the fallback gets its OWN named test (`test_sqlite_fallback_insert_where_not_exists`: seed a `running` row → second insert via the NOT-EXISTS path is refused; runs only when the dual-dialect render fails — loudly skipped otherwise), not a silent swap inside this case.
67. `test_unhandled_exception_returns_internal_error_shape` — **[A-8 / CF-6 doc-repair, v3.1]** force an unexpected exception inside a maintenance endpoint (stub a service method to raise a plain `RuntimeError` — NOT `MaintenanceError`) over the real router + `httpx.AsyncClient(transport=ASGITransport(app))` → assert **500** with the exact body shape `{"error": "internal_error", "message": <non-empty str>, "details": {}}`: pin the `error` literal, the `message` key present, and **`details` present-and-EMPTY** (the router catch-all per A-8's structured-dict binding). Companion negative: a `MaintenanceError` raised through the same path still renders its OWN code (no `internal_error` masking). Doc-only addition — documents the shipped `_call_service` catch-all; no code change prescribed.

### 4.4 Wiring pin extension

55. `tests/integration/test_checkpoint_cleanup_job_wiring_pin.py` — extend the AST scan: the single `CheckpointCleanupJob(...)` site must ALSO carry `run_lock=` and `runs_repo=` kwargs (silent-drop class guard, same rationale as the existing `message_metadata_repo` pin).

### 4.5 Before→after delta (test-matrix changes, per AM-16 + re-freeze item 3)

**ADD (12 new enumerated cases — corrected count [R-1, v3 fix pass]; was misstated as 10):**
- **9a** `test_manual_entry_point_orders_blobs_before_rows` — AM-2 ordering pin (unit).
- **11a** `test_conditional_insert_conflict_yields_409_no_row` — AM-5 conflict semantics + AM-6 no-refusal-row + INFO forensics (unit).
- **17a** `test_summary_skipped_cap_and_truncation_flag` — AM-10/AM-15 cap 1000 + `skipped_truncated` (unit).
- **56** `test_execute_succeeds_with_new_skip_pair_in_window` — re-freeze 3(b)/AM-16b.
- **57** `test_execute_excess_rows_no_mismatch` — re-freeze 3(c)/AM-2 **blocking regression pin** (silent over-deletion under the old D→E manual order — checks all pass, caught only here + case 44 [W-1 v3]).
- **58** `test_last_run_excludes_manual_dry_run` — re-freeze 3(d)/AM-9.
- **59** `test_origin_guard_matrix` — AM-1/AM-16e, 6-way parametrized (no-Origin, same-origin, localhost-family incl. `:4199`, TRUSTED_ORIGINS match, untrusted 403, `Origin: null` 403).
- **60** `test_kill_switch_off_integration` — AM-13/AM-16f (4×503 + availability 200 `kill_switched` + boot INFO).
- **61** `test_boot_sweep_unconditional_cas` — AM-7/AM-16g (unconditional, no age gate).
- **62** `test_dual_arm_contention_409_and_auto_skip` — AM-16h (both directions + INV-1 cell).
- **63** `test_409_body_run_id_adoption_contract` — AM-17/AM-16i (conflict names in-flight run; no `idempotency_key` anywhere).
- **64** `test_partial_index_render_both_drivers` — AM-5/AM-16j (dual-driver render or documented fallback).

**ADD (v3.1 [CF-6 doc-repair]):**
- **67** `test_unhandled_exception_returns_internal_error_shape` — A-8 catch-all 500 `internal_error` body-shape pin (integration; real router; see §4.3 case 67). BE total now **67** (was 66 at v3).

**REMOVE/REPLACE (nothing lost silently):**
- **53** `test_migration_mirror_idempotent` — **REPLACED by 64**: the `_ensure_postgres_columns` mirror no longer exists (AM-15 — new tables need no mirror; both-driver `create_all` is the build path), so "run the mirror twice" has no subject; the replacement proves what actually ships (dual-driver render).
- Idempotency-key cases: **none existed** in §4 (the prior plan only reserved the field in §5) — the §5 reserved-field default is **removed** (AM-17) and case 63 pins the absence.
- Refusal-row / `overlap_refused` cases: none enumerated as such; the reserved-status design they would have tested is **deleted** (AM-6) — cases 48-50 rewritten below cover contention without rows.
- Lock-only overlap semantics: cases 10-11, 15, 26, 32, 48-50 were specified against an asyncio.Lock-only gate — **rewritten** (in place, numbers retained) to conditional-INSERT semantics (lock = step 1, DB claim = the gate, 409 names the in-flight run, no row written).

**UPDATE (in place, same case numbers):**
- **12** run-id regex → `ckpt-\d{8}_\d{12}-[0-9a-f]{8}` (AM-8; was `ckpt-<ISO-Z>-<4hex>`).
- **15** auto-skip case → gate-held semantics: no row + DEBUG w/ in-flight run_id + `job.last_run` update (AM-4/Focus Area 6).
- **18** availability → `state` enum asserted (AM-13).
- **19** status shape → dual-flavor `blobs` keys + `skipped` (AM-10/AM-11).
- **20** `in_flight` → sourced from the `running` DB row (AM-9).
- **26** execute in-flight gate → conditional-INSERT conflict, 409-adoption payload (AM-5/AM-17).
- **28** gate order → Origin FIRST, kill-switch SECOND, then service gates (Contract v3 / INV-10).
- **29/31** 202 body → `advisory` + `expected_duration_ms_hint` always present; audit decision-input fields on the row (AM-12/AM-15).
- **32** dry-run gate → same single-flight gate (AM-4); response shape + canonical count fields (AM-10/AM-11).
- **34** error-body pins → include `maintenance_disabled` + `origin_not_trusted` (AM-1/AM-13).
- **35** boot recovery → sweep semantics: `interrupted` + `run_interrupted` + no age gate (AM-7; was `failed/interrupted_by_restart`).
- **36** kill-switch → availability 200 `kill_switched` + boot-read pin (AM-13).
- **37/38** availability endpoints → `state` field (AM-13).
- **43** → tagged re-freeze 3(a)/AM-16a with exact `ZERO_REFS_FAIL_SAFE` literal pin.
- **44** → 202 fields + decision-input audit-row asserts + AM-2 ordering exercised end-to-end.
- **46/47** → gate+repo kwargs naming; auto D→E order pin explicit (INV-9).
- **48/49/50** → rewritten to conditional-INSERT semantics (see REMOVE/REPLACE).
- **51** → execute-row audit-field asserts (AM-15).
- **54** → TEXT ISO ordering note (AM-15).

---

## 5. Open-Question Hooks — **RESOLVED, Contract v3 dispositions** (post-amendment; no open items)

| Item | OQ | Contract-v3 disposition (binding) | Where it bites |
|---|---|---|---|
| Error body shape | §6.1 | **RESOLVED — structured dict (plane.py) RATIFIED (A-8)**; binding for all 5 endpoints; `MaintenanceError` carries `{error, message, details}` | T5.1, T6.3, tests 34/52 |
| Kill-switch | §6.2 | **RESOLVED — AM-13**: `MAINTENANCE_ENDPOINTS_ENABLED` default `1`, boot-read; #2–#5 → 503 `maintenance_disabled`; `/availability` → 200 `state:"kill_switched"`; boot INFO when OFF; `MAINTENANCE_SERVICE_DISABLED` idea DROPPED (YAGNI) | T7, T6.3, tests 36/60 |
| Availability semantics | §6.3 | **RESOLVED — AM-13 state enum**: `ready\|backend_unsupported\|subsystem_disabled\|kill_switched`; `eligible` derived; transient startup race keeps frozen 503 `not_initialized` | T5.4, T6.3, tests 18/37/38 |
| Freshness window | §6.4 | **CONFIRMED — 300s** via `MAINTENANCE_DRY_RUN_FRESH_SECONDS`, env-configurable; keep 5 min | T7, T5.4, test 24 |
| last_run scope | §6.5 | **CLOSED — overridden by A-6/AM-9**: `last_run` = latest `succeeded\|failed` of `kind ∈ {auto, manual_execute}`; `manual_dry_run` never surfaces; `in_flight` = any `running` row | T4.2, T5.4, tests 20/54/58 |
| Concurrency limit | §6.6 | **CONFIRMED — single global lane** [AM-4/AM-5]: one gate for auto + both manual classes; first-acquirer wins (no manual priority); dry-run takes the gate | T3, T5.4, tests 11a/26/49/50/62 |
| Audit channel | §6.7 | **RESOLVED — one channel, AM-15**: `maintenance_runs` IS the audit trail (decision inputs + pre-state + outcome + failure); no separate channel | T4, T5.4, tests 29/51 |
| Idempotency key | §6.8 | **DROPPED — AM-17**: no `idempotency_key` field anywhere; 409-adoption replaces it (conflict body carries in-flight `run_id`) | T6.1, test 63 |

---

## 6. Phase-1 Risks & Edge Cases

- **R-1 SERIALIZABLE retry interplay.** The manual destructive path runs the blob DELETEs inside the adapter's SERIALIZABLE+retry wrap (INV-3, untouched). The gate serializes manual↔auto but NOT manual↔live-app-writes (Q1 decision: no daemon pause). Residual µs-window TOCTOU on the default saver pipeline is the same one PR4 adjudicated (see `checkpoint_prune_real_saver.py` retraction note) — bounded by the idle advisory + the retry wrap. Do NOT restate atomicity claims; the wrap converts 40001/40P01 into abort-and-retry, nothing more.
- **R-2 ZERO_REFS_FAIL_SAFE surfacing.** Skipped pairs MUST be visible (summary `blobs.skipped`, dry-run `skipped` — AM-10) so an operator doesn't read a small `would_delete` as "safe to run" when pairs were actually skipped for drift. Tests 43/56 pin it. `skipped[]` is informational only — NOT part of the confirm echo (AM-3; pair-count/skip-hash pins rejected: they false-refuse in a live daemon).
- **R-3 Run-registry memory bounds.** In-memory state is O(1): one lock context + at most one executing task reference (done-callback discard). All history/polling reads hit `maintenance_runs`. No unbounded dict of runs. Tests 29/44 exercise the lifecycle; a code-review checklist item pins "no module-level dict keyed by run_id".
- **R-4 `overlap_refused` is DELETED (AM-6) — not reserved.** Contention produces no row at all (the refused INSERT never commits — AM-5). One INFO log line with requester forensics is the only trace of a refused attempt (architect ruling: 404-noise churn from double-clicks outweighs attempt-audit value). Supersedes the prior draft's "reserved, never written" framing.
- **R-5 Timestamp discipline (INV-7 + AM-15).** Persisted columns are TEXT ISO via `now_utc_iso()` (`+00:00`); in-memory math (freshness age) uses naive-UTC `now_utc_naive()` against the parsed row timestamp — never `datetime.now()` (local TZ) and never aware-vs-naive comparison (mixed-frame trap; project critical-notes PG-UTC convention). Lexicographic ordering of TEXT ISO is chronological — `ORDER BY completed_at DESC` remains correct.
- **R-6 Restart mid-execute.** In-memory lock/task/ctx vanish; the row stays `running`. The boot sweep (T8, AM-7) flips it to `interrupted`/`run_interrupted` unconditionally (no age gate), so `/status` shows an honest terminal state and no phantom `in_flight`. Without it, `latest_completed_for_section` silently skips the row AND a stale `running` row would both linger and 409 the whole feature (the crash-window the AM-4 re-ordering + sweep jointly close).
- **R-7 Background task GC.** `asyncio.create_task` results must be referenced (the event loop holds only weak refs). The service keeps a task set with done-callback discard; manager shutdown cancels+awaits (T5.5).
- **R-8 PG `DESC` NULL ordering.** `ORDER BY completed_at DESC` defaults to `NULLS FIRST` on PG — a `running` row would win `last_run`. The repo filters to terminal statuses (T4.2), sidestepping both the NULL and the duplication-with-`in_flight` problem (unchanged under TEXT ISO — AM-15).
- **R-9 AST pin brittleness.** The structural pin keys on the local NAME `destructive`. Renaming the local (e.g. to `effective_destructive`) breaks the pin. Coding-standard note in T2: keep the name; the pin is the invariant's enforcement mechanism, not bureaucracy.
- **R-10 DSN hygiene.** All integration fixtures read `PG_TEST_*` only, scrub `ENSEMBLE_DB_DSN`, and assert `"ensemble_prod" not in dsn` before any connection (R-11 of the overview, enforced at fixture level).
- **R-11 SQLite partial-index render (new — architect Medium-High confidence).** SQLAlchemy dual-dialect `Index(postgresql_where=…, sqlite_where=…)` is supported but **unverified in this repo**. Case 64 is the Phase-1 verification gate; the documented fallback (plain conditional `INSERT … WHERE NOT EXISTS` + racy-belt caveat) must be pinned-if-used so a silent substitution cannot ship (AM-5).

---

## 7. Contract Feedback — **CLOSED: dispositions incorporated into Contract v3**

The Contract v3 (frozen 2026-09-27) resolves every item below; the dispositions are now binding implementation requirements reflected in the tasks above. Retained for review trail (originally: "for architect review — NOT silent deviations").

- **CF-1. Dry-run blob counts are not captured today.** `BlobPruneSummary`'s dry-run arm logs `would_delete` but accumulates nothing (`checkpoint_prune.py:203-217` vs `:235-236`). → **RESOLVED by AM-11 (A-2 RATIFIED)**: canonical fields `would_delete_count`/`would_free_bytes` added (T1.1).
- **CF-2. `/status` summary blob keys for destructive runs.** The frozen example showed the dry-run flavor; a destructive run rendering `would_delete: 12` is a lie. → **RESOLVED by AM-11 (A-5 RATIFIED)**: dual-flavor keys on every summary blob; destructive flavor emits `deleted`/`bytes_freed` with `destructive: true`; FE branches on `destructive` first (T1.3).
- **CF-3. `last_run` scope vs dry-run rows.** §6.5's "most recent of ANY kind" was written before dry-runs became persisted audit rows. → **RESOLVED by AM-9 (A-6 RATIFIED; §6.5 CLOSED)**: `last_run` = latest terminal row of `kind ∈ {auto, manual_execute}`; `manual_dry_run` never surfaces; dry-run history queryable via `runs/{run_id}` (T4.2). Splitting `last_auto_run`/`last_manual_run` remains the v2 option.
- **CF-4. Error body shape confirmation.** → **RESOLVED — A-8 RATIFIED**: structured dict (plane.py shape) binding for all five endpoints; migration.py's bare-string style is NOT used anywhere in this surface (T5.1).
- **CF-5. Timestamp wire format.** Contract examples showed `Z`-suffixed ISO; the repo's boundary emits `+00:00`. → **RESOLVED — A-7 CONFIRMED**: `+00:00` everywhere; examples swept in the overview; JS `Date` parses both; FE must not string-compare timestamps (T5.4).
- **CF-6. `skipped` array on the dry-run response.** → **RESOLVED — AM-10/A-3 RATIFIED**: `skipped: [{thread_id, checkpoint_ns, reason}]` on dry-run + `/status` + `/runs` summaries; reason enum = closed set `{ZERO_REFS_FAIL_SAFE, MAX_REFS_EXCEEDED}` (`checkpoint_prune.py:185-200`) ∪ open `ERROR:<ExceptionName>` (`:250-256`), documented extensible; capped at 1000 + `skipped_truncated` [AM-15]; informational only, not part of the confirm echo (AM-3). *(v3.1 [CF-6 doc-repair]: CF-6's ratification pass surfaced the `internal_error` catch-all doc-lag — resolved by the frozen-table 11th row in plan-overview, the router catch-all bullet in T6 7.3, and BE case 67; counts now 67 BE / 11 FE codes.)*
- **CF-7. Kill-switch error code.** → **RESOLVED — AM-13 (A-4 RATIFIED, renamed semantics)**: `maintenance_disabled | 503` in the frozen code table; availability renders `state:"kill_switched"` at 200; `MAINTENANCE_SERVICE_DISABLED` DROPPED (YAGNI).
- **CF-8. "Alembic migration" naming.** The repo has no Alembic — raw `.sql` applied by a SQLite-only runner, PG schema from `create_all`. → **RESOLVED — AM-15 (A-12 CONFIRMED)**: `-- MANUAL: TRUE` raw `.sql` = canonical doc only; table built by `SQLModel.metadata.create_all` on BOTH drivers; **no `_ensure_postgres_columns` entries** (T4.3/T4.4).

---

## 8. Definition of Done (Phase 1)

1. All three slices merged to `feature/maintenance-console`; whole-tree default partition green (`uv run python -m pytest` — integration/postgres excluded by default addopts).
2. `uv run python -m pytest tests/integration/test_maintenance_checkpoint_cleanup_api.py --override-ini="addopts=" -m "integration and postgres"` green against a local disposable-PG server (loud-skip is NOT a pass for items 44-50/56-64/67 — the destructive/overlap/AM-2/origin/catch-all proofs require real PG or the real router) [CF-6 doc-repair, v3.1: 67 added].
3. Existing prune suites green: `tests/unit/services/test_maintenance_prune_direct_anti_join.py` (incl. both structural pins) and `tests/integration/checkpoint_prune_real_saver.py` (when PG available). **Plus the 4 named new suites green at close-out [R-14, v3 fix pass]**: (1) `tests/unit/services/test_checkpoint_prune_destructive_override.py`, (2) `tests/unit/services/test_maintenance_checkpoint_cleanup_service.py`, (3) `tests/unit/services/test_maintenance_run_lock_and_capture.py`, (4) `tests/integration/test_maintenance_checkpoint_cleanup_api.py` — close-out census = whole-tree `grep -rln` over `tests/` for all four (unit-scoped checks miss top-level tests/).
4. Manual smoke of the five endpoints against a disposable-PG dev daemon (curl-level) — documented in the PR description, forming the Phase-3 live-spot-check baseline; Origin-guard refusal (untrusted Origin → 403) and kill-switch (4×503 + availability `kill_switched`) included in the smoke script.
5. **Re-freeze checklist item 3 SATISFIED** (this document): schema re-synced to the AM-15 FINAL schema; the four mandated BE cases present — (a) test 43, (b) test 56, (c) test 57, (d) test 58 — plus AM-16 additions 59-64; §5/§7 synced to the v2 dispositions (A-1 DROPPED, A-2/A-3/A-5/A-6/A-8/A-11 RATIFIED, A-4 RATIFIED-renamed, A-7/A-9/A-10/A-12 CONFIRMED, §6.5 CLOSED).
6. **No `idempotency_key` remains anywhere in the Phase-1 surface** (grep pin over schemas, tasks, tests — AM-17); 409-adoption payload proven by test 63.
7. **The AM-2 blocking regression pin exists and is green** (test 57 + ordering pin 9a): execute with excess rows present must NOT over-delete — it succeeds with byte counts matching the dry-run exactly; under D-first every server check passes and the failure is silent over-deletion, so this pin is the catcher, not the byte-equality gate [W-1, v3 fix pass].
