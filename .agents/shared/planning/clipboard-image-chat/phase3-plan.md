# Phase 3: 30-day retention/cleanup background service

> **Backend — Phase 3 of 6** for `feature/clipboard-image-chat`.
> Phase owner: `plan-worker-backend` (this file).
> Branch baseline: `feature/clipboard-image-chat` @ `307db932` (planning-only).
> Depends on: phase 1 (tmp-image store), independent of phase 2.

## Amendment log (architect rulings 2026-09-19)

Applied in this pass (per `.agents/shared/planning/clipboard-image-chat/architecture-recommendation.md` §8):

- **#15** — **DELETED `tmp_image_cleanup_enabled` kill-switch** (and `SERVICES_TMP_IMAGE_CLEANUP_ENABLED` env override) — contradicts the owner HARD POLICY codified in `daemon/services/job_lock_sweep.py` ("no kill-switch env var", full stop). The **retention-days knob IS the operator lever** (set huge to effectively disable). Internal constructor keeps an `enabled` param for **unit tests only**; hardcoded `enabled=True` at the boot anchor.
- **#15** — **Interval PINNED 3600s (hourly)** — was 86400 in this plan; register + brief say hourly. Hourly gives deletion latency ≤ retention + interval; scan is a cheap stat-walk.
- **#15** — **R10 activation-checklist line ADDED (verbatim from architect §7)** — the 30-day grace makes it soft in practice, but the edge is nearly free and the checklist line is mandatory.

### Round 2 (architect post-review rulings 2026-09-19)

Applied in this pass (per `architecture-recommendation.md` §"Post-review rulings"):

- **O5 — Anchor nits verified.** `JobLockSweepService` boot anchor `daemon/api.py:777-801` ✓; shutdown mirror `:1669-1682` ✓; `sweep_once` shape `:190-220` ✓; `ServicesConfig` field pattern `:1550-1568` ✓. No corrections required — phase-3 has zero functional changes from round 2 (amendments #26–#32, #36, #38, C3, freeze list, O2, O4 all land in phase 1 / phase 2; phase 3 owns only the sweep service). The DELETE∥sweep race test deferred annotation (O6) is in phase-1 plan Task 4b; it lands when phase 3 ships.

### Polish pass (cycle-2 review APPROVED-WITH-NOTES; rulings final; no re-review)

- No edits required for phase 3 in the polish pass — all 7 edits landed in phase 1 (edits #1, #6) and phase 2 (edits #2, #3, #4, #5, #7). Phase 3 owns only the sweep service (no two-channel design, no facade chain, no set_injection kwarg, no serializer union) — round 1 amendment log + round 2 O5 verification are sufficient.

---

## Objective

Add a **periodic background sweep** that reaps `data/tmp_images/` files
older than the configured retention window (default **30 days**),
modeled on the proven `JobLockSweepService` pattern
(`daemon/services/job_lock_sweep.py:128-241`). The service:

1. Boots in the lifespan **after** the existing `JobLockSweepService`
   (`daemon/api.py:777-801`) and **after** `TmpImageStore` (phase 1,
   Task 3).
2. Reclaims files whose mtime is older than `now - retention_days`.
3. Logs every deletion (with file id + age) so support can diagnose
   "why is my chat bubble broken?".
4. **ALWAYS-ON** — no kill-switch env var (architect §7 / amendment #15;
   contradicts the project owner's HARD POLICY on Batch A codified
   in `job_lock_sweep.py`; the retention-days knob IS the operator
   lever — set `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS` to a very
   large value to effectively disable reaping without removing the
   service). The unique failure mode of a toggle is silent permanent
   storage growth when flipped by accident.
5. **Graceful failure** — a transient filesystem error (EBUSY, EACCES,
   read-only mount) logs a WARNING and continues; the next tick
   retries. Mirrors `JobLockSweepService.sweep_once` defensive shape
   (`daemon/services/job_lock_sweep.py:201-214`).
6. **Activation** is rebuild + restart, same as all daemon-side
   always-on services.

Image loss is **acceptable** (text descriptions persist via phase 2's
conversion; the FE phase 6 ships an onerror placeholder for `404`
requests on cleaned-up files).

---

## Scope

### In scope

- New service file `daemon/services/tmp_image_cleanup_service.py`
  modeled on `daemon/services/job_lock_sweep.py:128-241`.
- Extend `ServicesConfig` at `daemon/config.py:1550-1568` (mirroring
  `job_lock_sweep_interval_seconds` shape) with **2 fields** (NOT 3 — no kill-switch per amendment #15):
  - `tmp_image_cleanup_interval_seconds: int = Field(default=3600, ge=1)` — **architect §7 PINNED hourly** (was 86400 in this plan; hourly gives deletion latency ≤ retention + interval; scan is a cheap stat-walk). Env override `SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS`.
  - `tmp_image_cleanup_retention_days: int = Field(default=30, ge=1)` (the configured age threshold; floor `ge=1`; cannot be 0 — that would delete everything on the first tick). Env override `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS`.
- New `TmpImageCleanupService` class with:
  - `__init__(tmp_image_store: TmpImageStore, *, enabled: bool = True, interval_seconds: int = 3600, retention_days: int = 30)` — `enabled` is an **internal-only param for unit tests**; the boot anchor hardcodes `enabled=True` (no env override, no public field).
  - `start()` — spawn the periodic asyncio task (mirrors
    `job_lock_sweep.py:144-166`).
  - `async stop()` — graceful cancel + await (mirrors
    `job_lock_sweep.py:168-188`).
  - `async sweep_once() -> int` — single deterministic tick (returns
    deleted count; mirrors `job_lock_sweep.py:190-220`). **Idempotent unlink**: `FileNotFoundError` swallowed silently (FE DELETE ∥ sweep race = expected traffic per architect §7); missing dir → WARNING + return 0.
  - `_run()` — periodic tick loop with `asyncio.CancelledError`
    handling (mirrors `job_lock_sweep.py:222-250`). Boot-sweep first-tick-immediate (timing vs manager startup irrelevant — fs-only).
- Boot anchor in `daemon/api.py` lifespan, **AFTER** `TmpImageStore`
  wiring (phase 1 Task 3) and the existing `JobLockSweepService`
  boot anchor (`daemon/api.py:777-801`). Mirror the boot log shape:
  `[TmpImages] cleanup service started: interval=<s> retention=<d>d`
  (**no `enabled=<bool>` field — service is always on; the knob is
  retention**).
- Shutdown mirror in `daemon/api.py`, **before** the existing
  `JobLockSweepService` shutdown (`daemon/api.py:1669-1682`). Mirror
  the graceful-with-WARNING shape.
- **Idempotent startup**: a second `start()` while the task is alive
  is a no-op (mirrors `job_lock_sweep.py:144-158`).
- **Health endpoint integration**: extend the phase-1 health endpoint
  (`GET /api/tmp_images` returning `{count, oldest_mtime}`) with a
  `cleanup` field
  `{interval_seconds, retention_days, last_sweep_at,
  last_sweep_deleted, last_sweep_error}` (**no `enabled` field** —
  service is always on; observability is via interval + retention).
  Per-tick deleted-count structured log on every successful reap.
- **R10 activation checklist** (architect §7, **MANDATORY**, verbatim):

  > BEFORE rebuild+restart with the sweep live: record oldest mtime in `data/tmp_images` (debug GET, enable env flag). If any file is ≥ retention_days old — or `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS` < 30 — deploy phase-6 FE dist FIRST; otherwise phase-6 must land within retention_days of activation. Verify FE dist hash post-deploy (daemon rebuild does NOT cover FE — cbb47e42 note). *(architect §7 wrote `SERVICES_TMP_IMAGE_RETENTION_DAYS`; normalized to the field name in this plan's Task 2 / Components.)*

### Out of scope (flagged for follow-up)

- **Persistent sweep ledger** — a `tmp_image_cleanup_runs` table that
  records every deletion. Today's plan logs to `daemon.log` (which is
  greppable). A ledger table is a separate observability effort.
- **Soft-delete grace period** — a "soft-deleted" state where the
  ref is removed from FE visibility but the bytes are retained for N
  days. The current design reaps the bytes; the FE accepts `404` after
  retention (phase 6 onerror placeholder). A grace period requires
  schema support that doesn't exist.
- **Per-user / per-tenant retention** — the default 30d is global. A
  per-user knob would require a `users` or `sessions` lookup table
  that doesn't exist today. Flagged for the same future effort as
  per-user scoping in phase 1.
- **Compression or offloading** — for an on-prem self-hosted tool
  with ≤3 images × 10MB per chat turn, disk usage is bounded and
  compression is not justified. Flagged for very-large-deployment
  follow-ups only.

---

## Components (file:line anchors — verified)

| # | Component | Anchor | Notes |
|---|---|---|---|
| 1 | `JobLockSweepService` template (the canonical pattern) | `daemon/services/job_lock_sweep.py:128-241` | `__init__` shape at `:128-142`; `start()` at `:144-166`; `stop()` at `:168-188`; `sweep_once()` at `:190-220`; `_run()` at `:222-250`. **Reuse every shape verbatim** — interval_seconds, `_task`/`_stopping` flags, `asyncio.CancelledError` handling, defensive Exception logging. |
| 2 | `ServicesConfig` field pattern | `daemon/config.py:1550-1568` (`job_lock_sweep_interval_seconds`) | Add **2 sibling fields** (interval, retention_days; architect amendment #15 — no `enabled` field). Each has a multi-line description (matching the convention at `:1553-1567`), a default, a `ge=1` bound, and a `SERVICES_*` env override path. The retention-days knob IS the operator lever (no kill-switch). |
| 3 | OrphanWatcherSweep age-knob precedent (grace_seconds) | `daemon/config.py:1474-1533` | Demonstrates how a "how-old" knob is described (multi-line, fail-fast at boot via pydantic ValidationError, ALWAYS-ON infrastructure). Reuse the description style. |
| 4 | Boot anchor (post-`JobLockSweepService` boot, post-`TmpImageStore` wiring) | `daemon/api.py:777-801` (the existing JobLockSweepService boot block) | Insert the new service boot **after** line 801 (after `app.state.job_lock_sweep = ...` and the log line). Place it in the same block — services grouped together. |
| 5 | Shutdown mirror (pre-`JobLockSweepService` shutdown) | `daemon/api.py:1669-1682` (the existing JobLockSweepService shutdown block) | Insert the new shutdown **before** line 1669 (before the JobLockSweepService shutdown). Order: cleanup first (so it stops touching the filesystem), then job_lock_sweep. |
| 6 | `TmpImageStore` API surface (needed by sweep) | Phase 1 Task 2 — `daemon/services/tmp_image_store.py` | The sweep needs: (a) `list_ids_with_mtime() -> list[tuple[str, float]]` (new method added in this phase); (b) `delete(image_id)` (already in phase 1). |
| 7 | Defensive sweep-once error handling pattern | `daemon/services/job_lock_sweep.py:201-214` | Wrap the entire `sweep_once` body in `try/except Exception` with WARNING log + return 0. Reuse verbatim. |
| 8 | First-tick delay convention | `daemon/services/job_lock_sweep.py:222-237` (`asyncio.sleep(self._interval_seconds)` AFTER `sweep_once()`) | The first tick runs **immediately** on `start()`. For tmp-image cleanup, this is **OK** (cheap scan) but flag the behavior in a Task: an operator who just deployed a fresh data dir may see a sweep delete nothing on boot (no files to delete yet — that's fine). |

---

## Tasks (ordered, with acceptance criteria)

| # | Task | Depends On | Acceptance criterion |
|---|---|---|---|
| 1 | Extend `daemon/services/tmp_image_store.py` (phase 1) with `list_ids_with_mtime() -> list[tuple[str, float]]`. Implementation: `os.scandir(self._dir)` over the store dir, filter to regular files, return `(entry.name, entry.stat().st_mtime)` for each. Skip the hidden file (`.gitignore` from phase 1 Task 10). | Phase 1 Task 2 | Unit test: empty dir → `[]`; 3 files → 3 tuples in insertion order (or any order — test should not over-specify); mtimes match `os.path.getmtime`. |
| 2 | Add **2 fields** (architect amendment #15 — kill-switch DELETED) to `daemon/config.py` `ServicesConfig` (around `:1550-1568`): `tmp_image_cleanup_interval_seconds: int = Field(default=3600, ge=1)` — PINNED hourly per architect §7 (was 86400 in this plan; hourly gives deletion latency ≤ retention + interval); `tmp_image_cleanup_retention_days: int = Field(default=30, ge=1)` (floor cannot be 0 — would delete everything on first tick). Each has a multi-line description matching the convention at `:1553-1567`. Env overrides: `SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS`, `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS`. **NO** `tmp_image_cleanup_enabled` env var (architect amendment #15 — owner HARD POLICY violation; retention-days IS the lever). | none | Unit test: default config loads (interval=3600, retention=30); out-of-range (interval=0, retention=0) fails fast at boot via pydantic ValidationError; env override works for both knobs; `SERVICES_TMP_IMAGE_CLEANUP_ENABLED` is **NOT** honored (asserting absence in the config schema prevents accidental re-addition). |
| 3 | Create `daemon/services/tmp_image_cleanup_service.py` modeled on `daemon/services/job_lock_sweep.py:128-241`. Class `TmpImageCleanupService(tmp_image_store, *, enabled: bool = True, interval_seconds: int = 3600, retention_days: int = 30)` — **`enabled` is internal-only for test seam (architect amendment #15); no public API path or env knob exposes it**. Methods: `start()`, `async stop()`, `async sweep_once() -> int` (idempotent unlink, `FileNotFoundError` swallowed silently per architect §7; missing dir → WARNING + return 0), `async _run()`. **Reuse verbatim** every defensive pattern (Exception logging, `_stopping` flag, `asyncio.CancelledError` handling). | Tasks 1, 2 | Unit test: `start()` then `stop()` round-trip; `sweep_once()` deletes old files only; out-of-range interval (negative) clamps to 1 in `__init__` (matches `job_lock_sweep.py:135`); `sweep_once` on a missing dir returns 0 with WARNING (not crash); FE DELETE ∥ sweep race → both succeed (idempotent unlink). |
| 4 | Wire boot anchor in `daemon/api.py` lifespan **after** `app.state.job_lock_sweep` is set (`daemon/api.py:801`). Construct the service from `config.services.*` (interval, retention) and `app.state.tmp_image_store`. **Hardcode `enabled=True` at the boot anchor** (architect amendment #15 — no env knob). Log line: `[TmpImages] cleanup service started: interval=<s>s retention=<d>d` (**no `enabled=` field — service is always on; the knob is retention**). | Tasks 2, 3 + Phase 1 Task 3 | Boot log present; service task is alive (probe via `app.state.tmp_image_cleanup._task is not None` in a debug assertion, optional). |
| 5 | Wire shutdown mirror in `daemon/api.py` lifespan **before** `app.state.job_lock_sweep` shutdown (`:1669-1682`). Use the same `getattr(app.state, "tmp_image_cleanup", None)` defensive pattern. WARNING on stop failure (mirrors `:1678-1681`). | Task 4 | Manual probe: SIGTERM → service stops cleanly; WARNING logged on forced kill. |
| 6 | ~~**STRIKE — kill-switch defensive guard** (architect amendment #15): the original Task 6 read "if `enabled=False` at `start()`, do NOT spawn the task. Log `[TmpImages] cleanup service DISABLED via config`" — this whole branch is REMOVED. The service is ALWAYS-ON. Internal unit tests inject `enabled=False` to exercise the constructor + `sweep_once()` without spawning the task; **there is no production code path that takes `enabled=False`**.~~ Replace with: add a unit test that proves the **internal** `enabled=False` short-circuit (test seam ONLY): `start()` does not spawn the task; constructor accepts the param; production boot anchor hardcodes `True`. | Task 3 | Unit test: `enabled=False` (test seam) → `start()` does not spawn task, no DISABLED log line emitted (that branch is gone); production boot always uses `enabled=True`. |
| 7 | Extend the phase-1 health endpoint (Task 6) to include `cleanup` field: `{interval_seconds: int, retention_days: int, last_sweep_at: iso8601|null, last_sweep_deleted: int, last_sweep_error: str|null}` (**no `enabled` field — architect amendment #15**; observability is via interval + retention). The service updates `last_sweep_at`, `last_sweep_deleted`, and `last_sweep_error` after every `sweep_once()` (in-memory state on the service instance; survives for the daemon lifetime). | Phase 1 Task 6 + Tasks 3, 4 | Integration test: after `start()` with no sweep yet, health returns `last_sweep_at=null`; after one `sweep_once()` (manual trigger in test), `last_sweep_at` is set and `last_sweep_deleted` matches; `cleanup` shape does NOT include `enabled` key (asserting absence prevents regression). |
| 8 | **Defensive sweep-once error handling**: wrap the sweep body in `try/except Exception` returning 0 with WARNING log + `last_sweep_error` field on the service (visible via health endpoint). Mirrors `job_lock_sweep.py:201-214` and `eligible_pending_sweep.py` patterns. | Task 3 | Unit test: forced exception in sweep body → 0 returned, WARNING logged, `last_sweep_error` populated. |
| 9 | **Audit logging** for every deletion: `logger.info(f"[TmpImages] reaped {count} image(s) older than {retention_days}d: {sample_ids}")`. The `sample_ids` is at most 5 ids (to bound log size); the full count is included. Mirrors the `JobLockSweepService` info-log shape at `:215-219`. | Task 3 | Manual probe: insert 3 old files, run `sweep_once()`, log line includes count + first few ids. |
| 10 | **Hardening**: when a file is `EBUSY` (locked by a reader — the FE's GET endpoint just opened it), the sweep MUST skip with WARNING, not crash. Implementation: catch `OSError` per-file in the sweep body, log WARNING, continue. | Task 3 | Unit test: chmod a file 000 (or `open(..., 'rb')` from a separate handle); sweep_once skips it, no crash, total count excludes it. |
| 11 | **Self-test seam** (for the tester): expose `sweep_once()` as a public, awaitable method (already done in Task 3). Document in the docstring: "tests call `sweep_once()` directly to avoid spawning the asyncio task". Mirror `job_lock_sweep.py:190-200` docstring. | Task 3 | Docstring present; tests can import + call without `start()`. |
| 12 | **Documentation**: add a one-paragraph note to the plan-overview (sibling file) summarizing retention defaults + the operator override knobs. No new docs/ file. | All | Note present in plan-overview. |

---

## Dependencies

### Backward (depends on)

- **Phase 1** (TmpImageStore) — the service needs `list_ids_with_mtime`
  and `delete` (Task 1 adds the former; Task 2 added the latter).
- **Existing config validation** — pydantic's `Field(ge=1)` failure path
  (verified at `daemon/config.py:1552, :1565`).

### Forward (is depended on by)

- **FE phase 6** (onerror placeholder) depends on the sweep's behavior:
  a `GET /api/tmp_images/<ref>` may return `404` after retention. The
  FE handles this gracefully (renders a "Image no longer available"
  card).

### Cross-cutting

- **Activated via rebuild + restart**.
- **No DB migration**.
- **Default retention 30 days** matches the project's "ephemeral by
  default" convention seen elsewhere (e.g. `_pending_injections`
  TTL = 3600s at `daemon/manager.py:2372`, :648 per RAG finding).

---

## Test strategy

### Unit

| Test | File | Verifies |
|---|---|---|
| `test_tmp_image_store_list_mtime.py` | `tests/unit/services/` | New `list_ids_with_mtime()` semantics: empty dir, mixed mtimes, hidden files excluded. |
| `test_tmp_image_cleanup_config.py` | `tests/unit/config/` | Default values; env overrides; out-of-range fail-fast (interval=0, retention=0). |
| `test_tmp_image_cleanup_service.py` | `tests/unit/services/` | `start`/`stop` round-trip; `sweep_once` happy path (deletes old, retains new); `sweep_once` per-file error tolerance (forced EBUSY skips); `enabled=False` short-circuits; interval clamp to ≥1. |
| `test_tmp_image_cleanup_service_logging.py` | `tests/unit/services/` | WARNING log on sweep exception; INFO log on successful deletions with sample ids. |

### Integration

| Test | File | Verifies |
|---|---|---|
| `test_tmp_image_cleanup_lifespan.py` | `tests/integration/` | Real lifespan: boot the daemon → service task is alive → SIGTERM-style shutdown → task is done. Mirrors `tests/integration/test_job_driven_enqueue_work_id_facade.py` lifespan shape. |
| `test_tmp_image_cleanup_health.py` | `tests/integration/` | After `sweep_once()`, GET /api/tmp_images (phase 1 Task 6) returns the populated `cleanup` field. |
| `test_tmp_image_cleanup_e2e.py` | `tests/integration/` | End-to-end: upload 3 images via phase 1 POST → backdate their mtime via `os.utime` → run `sweep_once()` → 3 files deleted; GET returns 404 for each id. |

### Facade-forwarding discipline

- **Not applicable** — no new kwargs on `InstanceManager` /
  `enqueue_message` / `set_injection` in this phase.

### Web-automation e2e (tester will run later)

The tester will need:

- A way to force retention expiry in tests: `os.utime(<path>,
  (time.time() - 31*86400, time.time() - 31*86400))` on uploaded
  files. This is the deterministic test seam.
- The health endpoint's `cleanup.last_sweep_deleted` field — the
  tester can assert on this count without needing to grep logs.
- A way to disable cleanup mid-test: use the **internal constructor param** `enabled=False` (architect amendment #15 — test seam only; no env knob) → service does not spawn task → no surprise deletions during the test run.

---

## Phase risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Sweep deletes a file that the FE is currently rendering** — a race between the sweep and an in-flight GET | Medium (user sees broken image; reload fixes it) | Low (FE GET is brief; sweep is daily; collision requires exact timing) | The sweep catches `EBUSY` per-file (Task 10) and skips with WARNING; the FE phase 6 ships an onerror placeholder for `404`. Net: a transient visual blip, never a crash. |
| 2 | ~~**Operator forgets the kill-switch exists and is surprised when retention is "too aggressive"**~~ **🔴 STRUCK (architect amendment #15 — no kill-switch exists).** The original risk row is REMOVED: there is no `enabled` toggle to forget; the retention-days knob is the only lever and its semantics are obvious (set huge to effectively disable). Replaced risk: "operator sets `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS=1` for testing and forgets to reset" → Low (UX complaint, not a crash) → Medium likelihood | Document the retention default (30d) prominently in the plan-overview note (Task 12) and in the OpenAPI schema description on the health endpoint. The 30d default is intentionally generous. |
| 3 | **Disk-full condition masks the sweep's logs** — if the disk is 100% full, log writes may fail, masking the "why is the disk full?" root cause | Low | Low | The sweep logs to `daemon_logger`, which writes to `data/logs/ensemble.log` (`daemon/api.py:59`). If the disk is so full that logs can't write, the system has bigger problems than tmp images. **Document**: a separate log-rotation concern is out of scope. |
| 4 | **The sweep scans a misconfigured `data_dir`** — if a future config change moves `data_dir` (e.g. per the facade-forwarding discipline), the sweep silently scans the wrong dir | Medium (sweep reaps images from the wrong filesystem) | Low (the lifespan passes `app.state.data_dir` — single source of truth, verified by phase 1 Task 8 boot log) | The integration test asserts the sweep scans the SAME dir as `TmpImageStore`; the boot log includes the resolved absolute path. Any drift surfaces in the log. |
| 5 | **Concurrent sweep + upload race** — the sweep is iterating `os.scandir()` while the upload is creating a new file | Low (no crash; POSIX file creation is atomic; the new file may or may not appear in the scan) | Low | The sweep iterates a snapshot from `os.scandir()` (returns DirEntries at scan time); a new file created mid-scan either appears (and gets aged-checked on its mtime — a fresh file is retained) or doesn't (will be picked up next tick). Either outcome is correct. |
| 6 | ~~**The kill-switch default is ON** — operators who never read the config may be surprised when their test data disappears after 30 days~~ **🔴 STRUCK (architect amendment #15 — no kill-switch).** Replaced risk: "operators who never read the config may be surprised when their test data disappears after 30 days" → Low → Low | Document in the plan-overview (Task 12) and in the OpenAPI schema description on the health endpoint. The 30d default is intentionally generous. |
| 7 | **Hard-coded retention_days vs runtime override** — if the operator changes the env var without restarting, the change has no effect | Low | Low | Document the restart-required behavior in the config field description (matching the convention at `daemon/config.py:1553-1567`). |

---

## Open questions (escalated to `decisions.md`)

1. **Retention default** — 30 days. The architect may prefer 7 (more
   aggressive) or 90 (more lenient). Recommended 30 — generous for
   most use cases; easily overridden via env var.
2. **Sweep interval** — **3600s (hourly, architect §7 PINNED)** — deletion latency ≤ retention + interval; scan is a cheap stat-walk. Earlier draft said 86400 (24h); struck per amendment #15.
3. **Health endpoint field naming** — `cleanup` vs `retention` vs
   `reaper`. Recommended `cleanup` (matches the existing service name
   pattern: `JobLockSweepService`, `OrphanWatcherSweepService`).
4. **Sweep on-disk ledger** — should the deletion log go to
   `daemon.log` only, or also to a `tmp_image_cleanup_runs` table?
   Recommended: log only for v1; table is a separate observability
   effort.