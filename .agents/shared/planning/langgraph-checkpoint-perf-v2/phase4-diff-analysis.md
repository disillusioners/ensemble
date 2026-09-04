# Phase 4 — Diff Analysis (T4.1 deliverable)

> Date: 2026-09-04 (UTC) | v2 HEAD (start): `7d9384e7`
> Branch: `feature/langgraph-checkpoint-perf-v2`
> Method: read-only `git show` against v1 `feature/langgraph-checkpoint-perf` (READ-ONLY).
> Pair analyzed: `f89ccacc` (PR4 feat) + `7a7998fe` (PR4 critical fix).

## Pair overview

| SHA | Subject | Surface | Insertion |
|-----|---------|---------|-----------|
| `f89ccacc7bedd517895357128fde6270ff0f7e23` | feat(perf): PR4 — C3 reference-aware checkpoint_blobs prune | 13 files / +2730 / -2 | mostly HOT + 5 clean-adds |
| `7a7998fe52a189af0b462e3ec2dae68e4bfa4100` | fix(perf): PR4 critical — serializable wrap + retraction + race tests | 5 files / +565 / -17 | 2 HOT + 2 helpers + 1 test + 1 runbook fold |

The two commits MUST land as a pair — `7a7998fe` carries:
1. The SERIALIZABLE wrap (destructive arm + 40001/40P01 retry, `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, 50ms·2ⁿ backoff, exhaustion returns `(0,0)` and skips without raising).
2. The atomicity-claim RETRACTION in the PG adapter docstring (cites `aio.py:82, 280-304, 393-399`).
3. The intra-process race disclosure in runbook §7.
4. `TestRealSaverRaceWindow` (new) + `TestRealSaverSerializableRetry` (new) — 7/9 → 9/9 binding-gate coverage.

Landing `f89ccacc` alone would re-introduce the 🔴 data-integrity finding.

---

## `f89ccacc` hunk shapes (PR4 feat)

### File-by-file

| File | Change | v2 anchor / lines | Conflict risk |
|------|--------|-------------------|---------------|
| `daemon/checkpoint_adapter.py` | +276/-2 | HOT: insert `_BLOB_ANTI_JOIN_PREDICATE` module-constant block at top (after `logger = …` at line 27); insert 4 abstract methods after `find_excess_checkpoint_groups` (anchor `:85-96`); insert SQLite stubs into `SqliteCheckpointerAdapter` (after `find_excess_checkpoint_groups` at anchor `:210`); insert PG impls into `PostgresCheckpointerAdapter` (after `find_excess_checkpoint_groups` at anchor `:378`). | ZERO-CONFLICT (architect §1.2 corrected): file byte-identical between v1-base `58260f35` and v2-base `2f80d45b` per `git diff 58260f35..2f80d45b -- daemon/checkpoint_adapter.py` (0 commits). |
| `daemon/checkpoint_perf.py` | +44/-0 | CLEAN: `log_blob_prune` function added. | ZERO (v2 file already byte-identical to v1 `fc908945` per `git diff fc908945 HEAD -- daemon/checkpoint_perf.py` — empty). The PR1 cherry-pick `87ad1018` already added `log_blob_prune` to v2. 3-way merge will skip this hunk as no-op. |
| `daemon/constants.py` | +11/-0 | LOW: insert 3 constants after `IDEMPOTENCY_KEY_TTL_HOURS` (anchor `:75` in v2). | LOW (architect §1.2 confirmed): 13 v2 commits touch this file but NONE are PR4 surface; all 4 flag names absent from v2 (`grep -n "CHECKPOINT_BLOB_PRUNE" daemon/constants.py` → 0). |
| `daemon/services/checkpoint_prune.py` | +267/-0 (clean add) | CLEAN: brand-new file. | ZERO. |
| `daemon/services/maintenance.py` | +45/-2 | HOT: change class docstring (4 → 5 ops); insert Operation E hunk in `execute()` after `_prune_per_thread_checkpoints()` call (anchor `:448`); insert `_prune_unreferenced_blobs` method body after `_prune_per_thread_checkpoints` (anchor `:678`). | ZERO-CONFLICT (architect §1.2 corrected): file byte-identical between v1-base `58260f35` and v2-base `2f80d45b` per `git diff 58260f35..2f80d45b -- daemon/services/maintenance.py` (0 commits). Defer-gate fix landed in `job_queue_service.py`, NOT maintenance.py. |
| `docs/runbooks/checkpoint-blob-prune-restore.md` | +191/-0 (clean add) | CLEAN: brand-new file. | ZERO. |
| `tests/helpers/checkpoint_prune_pg.py` | +174/-0 (clean add) | CLEAN. | ZERO. |
| `tests/integration/checkpoint_prune_real_saver.py` | +651/-0 (clean add) | CLEAN: binding-gate harness. | ZERO. |
| `tests/integration/checkpoint_prune_restore_rehearsal.py` | +170/-0 (clean add) | CLEAN: restore roundtrip. | ZERO. |
| `tests/integration/gate_suites/GATE_SUITES.txt` | +26/-0 | LOW: 4 new rows. | LOW: regenerated on v2 (T4.7). |
| `tests/unit/checkpoint_adapter/__init__.py` | 0 | EMPTY file (test pkg marker). | ZERO. |
| `tests/unit/checkpoint_adapter/test_direct_anti_join.py` | +435/-0 (clean add) | CLEAN: 11 anti-join unit tests. | ZERO. |
| `tests/unit/services/test_maintenance_prune_direct_anti_join.py` | +442/-0 (clean add) | CLEAN: 24 service-layer prune tests. | ZERO. |

### `f89ccacc` Hunk #1 — `_BLOB_ANTI_JOIN_PREDICATE` (module-level constant block in `daemon/checkpoint_adapter.py`)

```python
# Inserted between `logger = logging.getLogger(__name__)` (line 27) and the `class CheckpointerAdapter(ABC):` definition.
_BLOB_ANTI_JOIN_PREDICATE = """
      b.thread_id = $1
  AND b.checkpoint_ns = $2
  AND NOT EXISTS (
      SELECT 1
      FROM checkpoints c
      WHERE c.thread_id = b.thread_id
        AND c.checkpoint_ns = b.checkpoint_ns
        AND (c.checkpoint -> 'channel_versions' ->> b.channel) = b.version
  )"""
```

**v2 anchor verified:** `:27` is `logger = logging.getLogger(__name__)`, `:30` is `class CheckpointerAdapter(ABC):`. Exact-match 3-way merge expected.

### `f89ccacc` Hunk #2 — 4 abstract methods on `CheckpointerAdapter`

```python
@abstractmethod
async def find_all_thread_ns_pairs(self) -> list[tuple[str, str, int]]: ...
@abstractmethod
async def count_refs_for_blob_thread(self, thread_id: str, checkpoint_ns: str) -> int: ...
@abstractmethod
async def count_blobs_anti_join(self, thread_id: str, checkpoint_ns: str) -> tuple[int, int]: ...
@abstractmethod
async def delete_blobs_anti_join(self, thread_id: str, checkpoint_ns: str) -> tuple[int, int]: ...
```

**v2 anchor verified:** inserted AFTER `find_excess_checkpoint_groups` (lines 85-96) and BEFORE `raw_saver` property at `:98`. Exact-match 3-way merge expected.

### `f89ccacc` Hunk #3 — SQLite stubs (return `(0, 0)` with WARNING)

```python
async def find_all_thread_ns_pairs(self) -> list[tuple[str, str, int]]:
    async with self._saver.lock:
        cursor = await self._saver.conn.execute(
            "SELECT thread_id, checkpoint_ns, COUNT(*) as cnt "
            "FROM checkpoints GROUP BY thread_id, checkpoint_ns "
            "ORDER BY thread_id, checkpoint_ns"
        )
        rows = await cursor.fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

async def count_refs_for_blob_thread(self, thread_id, checkpoint_ns) -> int:
    # SQLite stub — 0 refs; never trips fail-safe (short-circuit before this)
    return 0

async def count_blobs_anti_join(self, thread_id, checkpoint_ns) -> tuple[int, int]:
    logger.warning(
        "count_blobs_anti_join: SQLite backend has no checkpoint_blobs "
        "table — blob prune is a no-op (PostgreSQL-only operation)"
    )
    return (0, 0)

async def delete_blobs_anti_join(self, thread_id, checkpoint_ns) -> tuple[int, int]:
    logger.warning(
        "delete_blobs_anti_join: SQLite backend has no checkpoint_blobs "
        "table — blob prune is a no-op (PostgreSQL-only operation)"
    )
    return (0, 0)
```

**v2 anchor verified:** inserted AFTER `SqliteCheckpointerAdapter.find_excess_checkpoint_groups` (anchor `:210`) and BEFORE `async def close` at `:215`.

### `f89ccacc` Hunk #4 — PG concrete impls (in `PostgresCheckpointerAdapter`)

4 methods matching the abstract signatures, each using `async with self._pool.acquire() as conn:` and the shared `_BLOB_ANTI_JOIN_PREDICATE` for the count/DELETE arms. `delete_blobs_anti_join` is the simple form in `f89ccacc` (READ COMMITTED; pool-acquire-then-fetch) — `7a7998fe` REPLACES it with the SERIALIZABLE wrap.

**v2 anchor verified:** inserted AFTER `PostgresCheckpointerAdapter.find_excess_checkpoint_groups` (anchor `:378`) and BEFORE `async def close`.

### `f89ccacc` Hunk #5 — `daemon/services/maintenance.py` Operation E

```python
# In execute() at line 448 (after _prune_per_thread_checkpoints):
# Operation E (Phase 1 C3): reference-aware checkpoint_blobs prune.
# Isolated per the plan — a failure in the blob bucket must NEVER
# break the retention prune above (which has already completed)
# or any subsequent maintenance cycle. prune_unreferenced_blobs
# itself never raises; this belt-and-braces wrapper guarantees
# the isolation even if that contract regresses.
try:
    await self._prune_unreferenced_blobs()
except Exception as e:  # noqa: BLE001
    logger.error(f"Unreferenced blob prune operation failed: {e}")

# And the method body at end of class (after _prune_per_thread_checkpoints, anchor :678):
async def _prune_unreferenced_blobs(self) -> None:
    """(E) Phase 1 C3 — reference-aware checkpoint_blobs prune (dry-run default).
    ...
    """
    from daemon.services.checkpoint_prune import prune_unreferenced_blobs
    await prune_unreferenced_blobs(self._checkpointer)
```

**CRITICAL:** the `except` clause uses `except Exception as e:` — NEVER `except BaseException:`. C-14 compliant. Verified by line in `git show f89ccacc -- daemon/services/maintenance.py`.

### `f89ccacc` Hunk #6 — `daemon/constants.py` 3 constants

```python
# Inserted after IDEMPOTENCY_KEY_TTL_HOURS (anchor :75) at v2:
CHECKPOINT_BLOB_PRUNE_DRY_RUN: bool = True  # default dry-run; set false only via env (CHECKPOINT_BLOB_PRUNE_DRY_RUN=0)
CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE: bool = False  # destructive kill-switch; env-overridden (CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1); default OFF
CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD: int = 100_000  # safety cap — skip pairs with more refs than this
```

`7a7998fe` adds the 4th constant (`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES: int = 3`) at the same insertion site (after `MAX_REFS_PER_THREAD`).

---

## `7a7998fe` hunk shapes (PR4 critical fix)

| File | Change | v2 anchor |
|------|--------|-----------|
| `daemon/checkpoint_adapter.py` | +144/-17 | REPLACES `delete_blobs_anti_join` body (destructive arm) with SERIALIZABLE wrap; adds the wrap rationale docstring block; ADDS `import asyncio` + `from daemon.constants import CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES` at top; adds a 2-line docstring fold to `count_blobs_anti_join`. |
| `daemon/constants.py` | +6/-0 | ADDS `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES: int = 3` after the 3 PR4 constants. |
| `docs/runbooks/checkpoint-blob-prune-restore.md` | +35/-0 | FOLDS the intra-process race disclosure into §7 (the "Flip the ladder" subsection). |
| `tests/helpers/checkpoint_prune_pg.py` | +67/-0 | ADDS `separate_pools` harness fixture (mirroring prod `create_postgres_checkpointer` topology). |
| `tests/integration/checkpoint_prune_real_saver.py` | +330/-0 (gross) | ADDS `TestRealSaverRaceWindow` + `TestRealSaverSerializableRetry` (7 → 9 binding-gate tests). |

### `7a7998fe` SERIALIZABLE wrap config (verified VERBATIM)

```python
retries_left = CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES  # = 3
attempt = 0
while True:
    attempt += 1
    try:
        async with self._pool.acquire() as conn:
            async with conn.transaction(isolation="serializable"):
                rows = await conn.fetch(
                    "DELETE FROM checkpoint_blobs b WHERE"
                    + _BLOB_ANTI_JOIN_PREDICATE
                    + " RETURNING OCTET_LENGTH(blob) AS n",
                    thread_id,
                    checkpoint_ns,
                )
        bytes_freed = sum(int(r["n"]) for r in rows if r["n"] is not None)
        return (len(rows), bytes_freed)
    except Exception as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate not in ("40001", "40P01"):
            raise
        if retries_left <= 0:
            logger.error(
                "[CheckpointPerf] blob_prune SERIALIZABLE_RETRY_EXHAUSTED "
                "thread=%s ns=%s — anti-join DELETE aborted with SQLSTATE %s "
                "on all %d attempts; skipping pair, zero rows deleted "
                "(safe direction; next maintenance cycle retries)",
                thread_id[:8], checkpoint_ns, sqlstate, attempt,
            )
            return (0, 0)
        retries_left -= 1
        backoff_s = 0.05 * (2 ** (attempt - 1))  # 50ms·2ⁿ
        logger.warning(
            "[CheckpointPerf] blob_prune serializable_retry "
            "thread=%s ns=%s attempt=%d sqlstate=%s (%s); retrying in %.0fms "
            "with a fresh snapshot",
            thread_id[:8], checkpoint_ns, attempt, sqlstate,
            "serialization_failure" if sqlstate == "40001" else "deadlock_detected",
            backoff_s * 1000,
        )
        await asyncio.sleep(backoff_s)
```

**Config verified EXACTLY per plan §T4.1:**
- ✓ `CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`
- ✓ 50ms·2ⁿ backoff (`0.05 * (2 ** (attempt - 1))`)
- ✓ Exhaustion returns `(0, 0)` (the `return (0, 0)` on retry-exhausted path)
- ✓ Skips without raising (the `return (0, 0)` is non-raising; the method never propagates SQLSTATE 40001/40P01)

### `7a7998fe` aput non-atomicity RETRACTION (verified VERBATIM)

The PG adapter's `delete_blobs_anti_join` docstring includes a "SERIALIZABLE wrap rationale" block that RETRACTS the false atomicity claim. Key citations:

```
aio.py:82 — psycopg autocommit + pipeline entry point (the default AsyncPostgresSaver path on PG14+)
aio.py:280-304 — default pipeline path commits blob upsert and checkpoint upsert as SEPARATE implicit transactions
aio.py:393-399 — non-pipeline fallback IS atomic
```

Verified VERBATIM in `git show 7a7998fe -- daemon/checkpoint_adapter.py`. Citations appear in 3 sites:
1. The wrap rationale block (PG `delete_blobs_anti_join` docstring).
2. The wrap rationale block — second sentence ("a µs-scale gap …").
3. The HONEST LIMIT paragraph ("lone READ COMMITTED racer does NOT trip SSI").

### `7a7998fe` runbook §7 race disclosure (verified VERBATIM)

Inserted into the existing PRE-ENABLE CHECKLIST §7 ("Flip the ladder") subsection. Key sentence: "the prune's anti-join DELETE takes its snapshot inside that gap — even single-process, because the maintenance task and graph turns share the process — it can see the new blob without the checkpoint row referencing it and delete the blob". Verified VERBATIM in `git show 7a7998fe -- docs/runbooks/checkpoint-blob-prune-restore.md`.

---

## Architect §1.2 corrections confirmed

The plan's "architect §1.2 correction" asserts:

> `git diff 58260f35..2f80d45b -- daemon/checkpoint_adapter.py` returns ZERO lines for `daemon/checkpoint_adapter.py` — byte-identical. Anchors `:85` (abstract-method anchor — `find_excess_checkpoint_groups`), `:378` (PG adapter), `:210` (SQLite) all intact.

**Verified:**
- `git log --oneline 58260f35..2f80d45b -- daemon/checkpoint_adapter.py | wc -l` → **0**
- `git log --oneline 58260f35..2f80d45b -- daemon/services/maintenance.py | wc -l` → **0**
- `git log --oneline 58260f35..2f80d45b -- daemon/constants.py | wc -l` → **13** (LOW conflict only — adjacent-inserts class; 4 PR4 flag names absent from v2)
- v2 `daemon/checkpoint_adapter.py`:
  - `:27` = `logger = logging.getLogger(__name__)` ✓
  - `:85` = `async def find_excess_checkpoint_groups(...)` ✓
  - `:210` = `SqliteCheckpointerAdapter.find_excess_checkpoint_groups` ✓
  - `:378` = `PostgresCheckpointerAdapter.find_excess_checkpoint_groups` ✓

**Conclusion:** cherry-pick expected to apply with auto-merge cleanly (defensive manual fix-up retained per plan §"manual-fix-up column retained as defensive fallback").

---

## V2 dependency surface (PR1 already brought `log_blob_prune`)

Verified `daemon/checkpoint_perf.py:168` is `def log_blob_prune(...)` — present on v2-tip. The PR1 cherry-pick `87ad1018` (manual re-apply of v1 `0db1a768`) already added this function. Therefore the `f89ccacc` hunk on `daemon/checkpoint_perf.py` is a no-op via 3-way merge (the file is already at the post-`f89ccacc` state from v1 `fc908945`; `git diff fc908945 HEAD -- daemon/checkpoint_perf.py` → empty).

---

## Binding-gate hardening (T4.9)

`7a7998fe` adds 2 new binding-gate tests:

| Test | Class | Purpose |
|------|-------|---------|
| `test_real_saver_race_window` (bidirectional) | `TestRealSaverRaceWindow` | Pre-existing referenced blobs byte-equal through interleaved multi-turn `aput`s + destructive prune. |
| `test_real_saver_serializable_retry` | `TestRealSaverSerializableRetry` | Two serializable participants → PG aborts (40001) → retry completes with referenced blob spared. |

Plus `tests/helpers/checkpoint_prune_pg.py::create_postgres_checkpointer_topology` (new `separate_pools` fixture mirroring `create_postgres_checkpointer` prod topology).

**Result:** binding-gate coverage goes 7/9 → 9/9 GREEN on real PG 14.22 (per v1 `7a7998fe` baseline).

---

## Risk audit

| # | Plan risk | Status |
|---|-----------|--------|
| 1 | Partial pair landing | Mitigated: T4.4 cherry-picks as a 2-commit sequence; binding-gate (T4.9) is the regression boundary. |
| 2 | SERIALIZABLE config drift | Mitigated: this analysis captures the verbatim config; T4.9 binding gate verifies via `TestRealSaverSerializableRetry`. |
| 3 | Docstring retraction lost | Mitigated: T4.10 grep guard #6 verifies every `atomic` mention cites the retraction + `aio.py` line numbers. |
| 4 | Operation E conflicts with v2's defer-gate idle-gate work | Mitigated: architect §1.2 confirms ZERO-CONFLICT (v2 file byte-identical to v1-base). |
| 5 | PG version drift | Mitigated: Phase 0 T0.2 + T0.3 verified PG ≥14.22; T4.9 re-verifies at binding-gate time. |
| 6 | PR4 docs format drift | Mitigated: T4.5 visual diff vs v2's `.agents/shared/conventions.md`. |
| 7 | Mission stale-fixture regression | Mitigated: T4.10 includes the 7-node quarantine family. |
| 8 | WC-wake kill-switch state broken | Mitigated: T4.10 includes `tests/services/test_instance_messaging_queue_routing.py`. |
| 9 | Runbook §2 query accidentally executed against `ensemble_prod` | Mitigated: T4.9 binding gate is on disposable PG only; runbook §2 verification is a READ-ONLY SELECT. |
| 10 | Operation E's `try/except Exception` swallows `CancelledError` | Mitigated: T4.4 verifies `except Exception as e:` (NEVER `except BaseException:`). |

---

## Acceptance preflight

| Plan §T4.1 acceptance | Status |
|----------------------|--------|
| Diff-analysis file exists | ✓ this file |
| SERIALIZABLE wrap config documented | ✓ (`CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES=3`, 50ms·2ⁿ, exhaustion `(0,0)` skips without raising) |
| Docstring retraction documented | ✓ (cites `aio.py:82, 280-304, 393-399`) |
| Runbook §7 intra-process race disclosure documented | ✓ |

**Ready for T4.2 (create `daemon/services/checkpoint_prune.py`).**