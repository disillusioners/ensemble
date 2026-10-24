# wedge-capture — Architecture Recommendation (enrichment review)

**Commission:** wedge-capture evidence instrumentation (incident 2026-10-22 follow-up; stage-only).
**Reviewed plan set:** `plan-overview.md` + `phase1/2/3-plan.md` + `recurrence-evidence-matrix.md` (D1–D54 / T1–T41 / AC1–AC59 / SC-1..15) — reviewed **in this worktree**.
**Authoritative tree:** `/home/nea/ensemble-src-wt-wedge-capture` @ `639b7129b` (= `origin/latest` at plan time). `/home/nea/ensemble-src` never touched.
**Date:** 2026-10-22 (UTC). **Mode:** Standard Design, 3 parallel reviewers.
**Reviewers:** `ebf5508e` (resilience-design — focus 1+2, incl. host CPython 3.12.3 empirical measurements), `fe4d0262` (data-flow-design — focus 3+4+7, incl. an empirical sqlite probe), `768bebc1` (structural-design — focus 5+6+8; replacement worker — first attempt `92469900` died on a deprecated provider model before reporting; one re-dispatch, per escape valve).

---

## 1. Executive verdict

**The plan is SOUND-WITH-CHANGES.** Every architectural direction the leader settled holds under stress:
the external daemon-thread watchdog survives the S1 GIL shape (measured), `faulthandler` cross-thread dumps
are safe, the one-shot capture connection is genuinely independent of the saver pool, the boot hook can be
made additive-only, the migration is next-safe and rollback-safe on verified evidence, and the seal /
forbidden-surface scans are clean (0/35 intersection).

The changes are **specification gaps, not design errors** — but six of them are release-blocking-class and
MUST be folded into the plan before the implementation lane starts (§5). The three most consequential:

1. **D4's lag-probe semantics are undefined exactly where the instrument lives or dies** — unset-slot
   initialization, never-fired samples, histogram overflow, and the one-post-per-tick mechanic (which makes
   AC4 racy and queues ~1 callback/s during a long wedge). A naive "skip when no callback ran" implementation
   would **silently disable the lag trigger on a fully starved loop — the exact H2 shape**.
2. **D21's canonical pool counters do not map to the pinned `psycopg_pool 3.3.1`** — `checked_out` is not a
   native key (must be derived), `pool_size_total` is ambiguous, and a checkout-timeout counter does not
   exist natively. Shipping the plan's names verbatim puts fabricated numbers into the pool-saturation
   evidence — the instrument would mis-state the hypothesis it exists to test.
3. **Retention as specified leaks orphan captures past every cap** (D23 computes caps from the table; a
   missed row write hides the directory forever) — a direct reprise of the unbounded-growth failure mode this
   commission cites, plus the pin ceremony's "eight files" is stale (the true set is **13 files**).

---

## 2. Per-area verdict table

| # | Focus area | Verdict | One-line driver |
|---|---|---|---|
| 1 | Capture-during-starvation mechanics | **SOUND-WITH-CHANGES** | Core premise holds (watchdog wake latency median 6.28 ms under a pure-Python spin, measured); D4/D21/D23 gaps are blocking-class specs, not design flaws |
| 2 | Thread-safety audit of collectors | **SOUND-WITH-CHANGES** | All reads classified safe except ONE unstated hazard: `logging` handler locks on the tick path (deadlock class); `readiness_composite` is replaced-atomically (safe), `checkpoint_perf` lock is new and append-only |
| 3 | Boot-record + OFF-branch hook | **SOUND-WITH-CHANGES** | One UNSOUND sub-item: a top-level import of the new package inside the boot-pass import envelope can silently disable **auto-resume** (settled-item violation) — fixed by call-site write + lazy import |
| 4 | Wiring + lifecycle | **SOUND** | Anchors corrected (start `api.py:2712-2718` outside the `try:` at `:2589`; stop = house detached-join form); SIGTERM-only and pause/cancel non-interaction verified by grep (0 hits) |
| 5 | Category / pin ceremony | **UNSOUND as written → SOUND after A12** | D30's "eight files" is false: the tree has **8 exact-set pin sites / 13 files**; MCP stamping is a non-issue; adjacent fail-open seam (`_tool_category` attribute) closed by amendment |
| 6 | Test realism | **SOUND-WITH-CHANGES** | 2-leg simulation insufficient (sleep-block releases the GIL — does not test starvation mechanics); 3-leg matrix required + 6 assertion gaps + 4 house-trap gaps |
| 7 | Migration + rollback | **SOUND-WITH-CHANGES** | Prefix `20261022_000002` next-safe NOW (108 files, all distinct — verified); D49's four legs verified in-tree; AC20 as written is **empirically unprovable** (fresh-SQLite dies at pre-existing `20260714_000001`) |
| 8 | Forbidden surfaces | **SOUND / CLEAN** | 0/35 `SEALED_SHA_BASELINES` intersection with the full touch list; zero forbidden-path touches; `checkpoint_perf` unconditional-observe is house-precedented (`:93-99`) with filter + measurement added |

---

## 3. Consolidated amendments (merged, deduped, renumbered)

Numbered A1–A19. Each cites the owning D/T and the reviewer(s) that found it. Severity: 🔴 must land before
implementation; 🟡 should land with the phase it belongs to; 🟢 advisory.

### A1 🔴 — AMEND-D4 + NEW D4b (lag-probe semantics + wall-time sustain) [reviewer A]
Replaces the D4 mechanics (thresholds/reasons unchanged):
- **(a) Initial state:** the stamp slot starts UNSET; the first tick emits NO numeric sample and sets
  `lag_probe_state="initializing"`; an unset slot is never rendered as a number, never as healthy, and never
  evaluated by the trigger.
- **(b) At-most-one-outstanding:** post `loop.call_soon_threadsafe(probe_cb)` ONLY when no probe is
  outstanding. Slots `outstanding_posted_at: float|None`, `last_exec_at: float|None`; `probe_cb` (on the
  loop) clears `outstanding_posted_at` and sets `last_exec_at = monotonic()`. At tick T: if
  `outstanding_posted_at is not None` → `lag_s = T − outstanding_posted_at` (true lower bound of loop
  delay); else post, set `outstanding_posted_at = T`, and sample `lag_s = T − last_exec_at`. One-post-per-tick
  is REJECTED (unbounded ready-queue growth during a wedge, burst-on-unblock, racy peak — AC4 is not
  reliably satisfiable under it).
- **(c) Never-fired = a SAMPLE, not a skip:** a tick with no loop execution yields a sample valued at the
  elapsed since the last observed execution; never skipped, never 0. A fully starved loop yields a
  monotonically growing series and the sustain window ACCUMULATES across it.
- **(d) Histogram:** `lag.json` carries `samples / max / p95 / p50 / overflow_count / loop_unresponsive_s`
  (current outstanding duration at capture) with an explicit top/overflow bucket (values CLAMPED into it,
  never dropped).
- **(e) Degraded/stopped:** `manager._loop is None` or `loop.is_closed()` → `unknown`/`stopped`, never a
  number, never a crash. `call_soon_threadsafe` raising `RuntimeError("Event loop is closed")` → mark probe
  `stopped`, stop posting, do NOT inflate `missed_samples`.
- **(f)** `watchdog_tick_delay_s` stays a secondary GIL-contention signal, never a trigger input.
- **D4b (sustain is WALL-TIME):** the sustain test = elapsed wall time from first to last consecutive
  at/above-threshold sample ≥ `sustain_s`, so behavior is identical at 1 s or 5 s tick cadence and the fully
  starved case fires deterministically.

### A2 🔴 — AMEND-D21 (pool-counter mapping to psycopg_pool 3.3.1) [reviewer A; C-corroborated as unverified-in-tree]
Read `get_stats()` from the pool object resolved ONCE at watchdog start. Exact mapping:
`pool_checked_out` = **derived** `pool_size − pool_available` (clamped ≥0; NOT a native key);
`pool_size_current` ← `pool_size` (current `_nconns`); `pool_max` ← `pool_max` (ceiling, 5);
**`pool_size_total` is RETIRED** (ambiguous); `pool_waiting_clients` ← `requests_waiting` (native, async
pool only); `pool_checkout_timeouts` = **always `None`** + `pool_checkout_timeouts_reason=
"not_exposed_by_psycopg_pool_3.3.1"` (`requests_errors` is a superset — any waiting exception plus
`TooManyRequests` — and must NOT be presented as timeouts); `pool_requests_errors` ← `requests_errors`
(native, own name). Absent field ⇒ `None` + reason, never `0`. `pool_counters_source` =
`"psycopg_pool.get_stats"` | `"unavailable:<reason>"` (`not_postgres_backend` / `checkpointer_none` /
`no_pool_attribute`). **No `pg_stat_activity`-derived fallback ships** (empty `application_name` makes it
coarse; a derived count would be a fabricated number in an evidence artifact — the gap is documented, not
papered over).
**CROSS-PHASE CONTRACT:** plan-overview §4.1's canonical-name ledger, phase2 D37/T24 status fields, and the
sibling-matrix adoption (T39) must take the amended vocabulary in the SAME commit; T39 records the
reconcile note (do not edit the sibling file).

### A3 🔴 — AMEND-D23 + T11/T14 + T13/T12/D10 (retention authority + reconciliation + UNIQUE) [A + B, independently converged]
- **Retention is DIRECTORY-authoritative:** count/age/disk caps are computed by walking
  `<data_dir>/wedge-captures/*/` (mtime sort), never from the table alone; prune oldest until within caps,
  then delete corresponding rows BY `capture_dir`. Table `file_count`/`total_bytes` are metadata, not the
  authority. AC21 restated: holds with the row write FORCED to fail (≤3 dirs AND ≤3 rows).
- **UNIQUE:** model + `.sql` carry `capture_dir TEXT NOT NULL UNIQUE`, `capture_id TEXT NOT NULL UNIQUE`,
  and `CREATE UNIQUE INDEX IF NOT EXISTS idx_wedge_captures_capture_dir …` (the .sql gains this alongside
  the 2 planned indexes; still +1 migration file, still `CREATE … IF NOT EXISTS` only).
- **Idempotent reconcile:** on row-write failure write a sidecar `<capture_dir>/.pending_row.json`; the
  reconciler (watchdog start + before each capture) scans sidecars and re-inserts with
  `ON CONFLICT (capture_dir) DO NOTHING` over the own one-shot connection, deleting the sidecar on success.
  Still-missing rows remain visible via the D12/D40 filesystem fallback.

### A4 🔴 — AMEND-T15 (the tick body must not log) [reviewer A — hazard absent from the plan]
No logging on the 1 Hz tick success path. Log only on state transitions (trigger fired / capture
started/failed / probe stopped) and inside capture steps (off the tick path). The tick's `except Exception`
branch RECORDS into `missed_samples` and continues — it does not log. Rationale: `logging` handler locks
are held across the write; a loop thread blocked writing to a full stdout pipe while holding the handler
lock parks the watchdog's `logger.*` call forever — the watchdog would die exactly when needed.

### A5 🟡 — AMEND-T7 (resolve-at-start + faulthandler hardening) [reviewer A]
Resolve ONCE at watchdog start (never inside the capture deadline): the pool object
(`manager.checkpointer.raw_saver.conn` — the retry proxy's `__getattr__` resolves `.conn` to the
`AsyncConnectionPool`; SQLite backend ⇒ counters `None` + reason), the hostname (NSS lookup), and the
capture-root path. `faulthandler.dump_traceback` targets a REAL file opened with `open(path,"w")`
(fileno-bearing; `io.StringIO` raises `UnsupportedOperation` — measured), is flushed, and every exception
(`io.UnsupportedOperation`, `ValueError`, `OSError` incl. ENOSPC) is swallowed → `stacks.txt` records
`status: "failed:<ExcName>"`. `ru_maxrss` is a PEAK — label `rss_peak_kb`, never `rss_kb`. The capture
DB step wraps `psycopg.connect` in its own step budget (`connect_timeout` covers TCP, NOT DNS).

### A6 🟡 — AMEND-D19 + NEW D25 (readiness snapshot semantics) [A + B]
`app.state.readiness_composite` is published by REPLACEMENT (`api.py:3657`; Starlette `State` is lock-free)
— safe to read cross-thread. Read defensively: `list(composite.reasons)` under `try/except` ⇒ `[]`;
`composite_age_s = now(UTC-aware) − composite.checked_at` — `checked_at` is **tz-aware** while the boot
epoch is **naive-UTC**: never subtract one from the other; `stale` when age > 2× refresher interval
(default 10 s ⇒ 20 s, `daemon/config.py:2617-2619`). **D25:** the composite is `None` until the first
refresher tick (`api.py:2455` → `:3657`) ⇒ the `readyz.json` artifact is OMITTED when `None`
(absent-when-N/A, D37), never `null`, never a crash.

### A7 🟡 — AMEND-T16 (stall-ledger scope + filter + measurement) [A + C merged]
The new lock in `daemon/checkpoint_perf.py::log_saver_op` is held for the APPEND ONLY (never across the
timed op — today's call order already guarantees this: `time_saver_op` awaits the op then calls
`log_saver_op` in `finally`); the capture reads `list(deque)` under the same lock. The append is explicitly
gated on `duration_ms >= 5000` (the settled slow-saver cut) so the fast path is one int comparison. Pin: a
synthetic 6 s op must NOT hold the lock. Disclosure + cheap measurement: timeit 100k `log_saver_op` calls
with logging disabled, assert delta < ~1 µs/op, recorded in the evidence pack. (House precedent for
unconditional-but-bounded observe outside the log gate already exists at `checkpoint_perf.py:93-99`.)

### A8 🔴 — AMEND-T8/T9 (boot record: call-site write + blast-radius elimination) [reviewer B + architect rulings]
- **Write site = the `api.py` call site (`:2656-2666`), NOT 8 in-body return sites.** The call site is the
  only place holding both `ContinueResult`s and the resolved `data_dir`; the sweep's return is currently
  DISCARDED at `:2661` and MUST be captured as `sweep_result`. One file
  `<capture_root>/boot-records/<epoch>.json`; passes' counters under `passes.autocontinue` /
  `passes.orphan_sweep`; second write is a read-modify-write of the same file (single writer, sequential).
  Filename = `boot_epoch.strftime('%Y%m%dT%H%M%S%fZ')` (colon-free, matches D9); timestamps inside are
  explicit ISO strings with literal `Z` (naive-UTC frame; aware datetimes converted to naive-UTC first).
  Write = tmp-in-same-dir + `os.replace`; no fsync (diagnostic artifact).
- **Single-read (BC-1):** each pass computes `enabled = _auto_continue_enabled()` ONCE at its first line
  (`:318-321` main / `:678-682` sweep) and carries `mode` in its result; the recorder consumes the result
  and MUST NOT re-call `_auto_continue_enabled()`. `mode=` is added to exactly these existing log sites —
  main OFF `:323-327`, main success `:462-465`, sweep OFF `:684-688`, sweep success `:830-836`, sweep
  summary `:865-879` — and NO other (an idle ON boot emits no main-pass line; the RECORD is the primary
  carrier — documented).
- **Intent record:** a pre-pass record (mode + boot_epoch, `mode_source: "call_site_pre_pass"`) is written
  BEFORE `:2656` so a pass that raises still leaves the mode on disk; the pass result (labelled
  `mode_source: "pass"`) overwrites it.
- **Never-raise + blast radius (RB-1):** the recorder is ONE `try/except Exception` around build+write
  (WARN only); the recorder imports the record module LAZILY
  (`try: from daemon.services.wedge_capture.boot_record import … except Exception: … = None`) because
  `api.py:2652` imports the boot-pass module inside a try whose except at `:2667` only WARNs — a top-level
  ImportError of the new package would silently disable BOTH auto-continue passes. **A pack asserts the
  boot-record module imports no psycopg/sqlmodel/daemon.config; `daemon/services/wedge_capture/__init__.py`
  is EMPTY (stdlib-only).**
- **Path:** the call site threads the resolved absolute capture root (from the `data_dir` local at
  `api.py:547`) into the recorder; the ENV chain (`ENSEMBLE_WEDGE_CAPTURE_DIR → ENSEMBLE_DATA_DIR →
  DATA_DIR → ./data`, byte-identical to `api.py:547`'s chain) is the fallback for direct/unit invocation
  only — `app.state.data_dir` is unreachable from the pass signature and must not be assumed.
- `raw_env_value` recorded VERBATIM + a `mode_semantics` field documenting the literal-`"0"`-only disable
  rule (`=false`/`=off`/`=no` mean ON — `:156`).

### A9 🟡 — AMEND-T17 (start/stop anchors, corrected citations) [reviewer B; A-corroborated]
START: `daemon/api.py:2718` (immediately after `upgrade_journal_sweep.start()`), OUTSIDE the `try:` at
`:2589` (inside it, a raising start would be misattributed to the upgrade-journal envelope), with its own
`try/except Exception` + own log line; `app.state.wedge_capture_watchdog = watchdog`. STOP: at `api.py:3268`
(right after `app.state.resource_monitor = None`) using the HOUSE form — `stop()` (sets the Event) then a
**DETACHED** join thread `threading.Thread(target=watchdog.join, kwargs={"timeout": 5.0}, daemon=True).start()`
(precedent `api.py:3246-3262`; never an inline join on the loop). The capture loop checks the stop event
BETWEEN capture steps (a 20 s capture cannot hold teardown); `stop()` idempotent, never raises. Citation
fix: `manager._loop` is set at `manager.py:3357` INSIDE `InstanceManager.initialize()` (def `:3336`),
which `api.py:727` awaits — i.e. available from `:727`, before `setup_worker_pool()` (`:780`) and the boot
passes (`:2656`). Also: the model registers via explicit `WedgeCapture.metadata.create_all(manager.engine)`
(precedent `api.py:986-1003`) because `manager.__init__` already ran `create_all` at `manager.py:802`
before any api-level import; the model import defines the class only (no engine/session side effects).

### A10 🔴 — AMEND-T13/AC20 (PG-only boot acceptance; fresh-SQLite is unprovable) [reviewer B — empirically verified]
Replace "applies cleanly on a scratch SQLite/PG boot" with: applies cleanly on a scratch **PG** boot; the
`.sql` is present, prefix-unique, `IF NOT EXISTS`-only; migration count +1 (**108 → 109**; the dispatch's
"106→107" was stale). Fresh-SQLite is NOT an acceptance gate — it dies at the PRE-EXISTING
`20260714_000001` (`ALTER TABLE … DROP CONSTRAINT` ⇒ `OperationalError near "CONSTRAINT"` on sqlite 3.45.1,
empirically reproduced; second blocker `20240101_000001`). SQLite assertions: file exists, parses
(`MigrationFile.parse`), UP body has no non-`IF NOT EXISTS` DDL. Staging notes: `ensemble.spec:12-13` bundles
migrations automatically; `stage.sh:316-317` greps FULL history ⇒ `CONTAINS_CONTRACT_PHASE=true` already at
base ⇒ stage must carry `ENSEMBLE_ROLLBACK_SAFE=1` explicitly (S-7 already requires it — keep).

### A11 🔴 — AMEND-D30/T23/RK-9 (the TRUE 13-file pin ceremony) [reviewer C]
D30's eight-file set is STALE (5 lanes of drift; the registry header itself says "derive by GREPPING THE
TREE — do NOT trust this count", `_tool_registry.py:320-327`). The true ceremony (ONE commit,
`git show --stat` = exactly these):

| # | File | Edits |
|---|---|---|
| 1 | `daemon/tools/_tool_registry.py` | set `:329` + `CATEGORY_MODULES :941` + `KNOWN_TOOL_NAMES :1200` + refresh stale header (`:300-327` says "EIGHT pins"/"14 today") |
| 2 | `tests/unit/tools/test_upgrade_registration.py` | exact-set `:152` + member |
| 3 | `tests/unit/tools/test_attestation_registration.py` | exact-set `:189` + member |
| 4 | `tests/unit/tools/test_secrets_registration.py` | exact-set `:226` + member |
| 5 | `tests/integration/test_maintenancer_spawn_resolves_tools.py` | exact-set `:358` **AND** maintenancer `tools.allow` exact pin `:125-141` (append 9th entry) |
| 6 | `tests/integration/test_service_tool_flag_off_byte_identical.py` | exact-set `:823` + 5-site comment `:792-806` |
| 7 | `tests/unit/test_board_registration.py` | set `:68`, `len==15→16` `:197`, **trailing-array pin `:245-247`** (ari allow-after-board == `["self-alarm","view-write","board-link","mcp_rpc","wedge-capture"]`) |
| 8 | `tests/unit/test_self_alarm_registration.py` | set `:74`, len `:197` |
| 9 | `tests/unit/test_instance_ops_registration.py` | set `:76`, len `:178` |
| 10 | `tests/unit/test_wedge_capture_registration.py` (NEW) | full pack (T34 + A13) |
| 11–13 | `agents/{ari,leader,maintenancer}/meta.json` | append `"wedge-capture"` to `tools.allow` |

Non-editing gates verified safe under this shape (MUST pass in the same commit, not edited): frozen-tool-name
discovery (`test_frozen_tool_name_discovery.py:223-236` — forces CATEGORY_MODULES + KNOWN_TOOL_NAMES
co-commit); registry literal-order chain (`test_board_registration.py:199-206` — safe ONLY appending AFTER
`"board-link"`); neighbor pin (`test_set_instance_tunable.py:106` — `wedge_capture_*` sorts after
`shared_meta_kv`); baby-template scan; membership pins. Shell packs: 0 `PRIVILEGED` hits (no edits).

### A12 🟡 — AMEND-T22 (co-commit + path form) [reviewer C]
`CATEGORY_MODULES["wedge-capture"] = "daemon.tools.wedge_capture"` lands in the SAME commit as the
`KNOWN_TOOL_NAMES` additions (the no-drift gate is an exact `==`); the value must be a `daemon.tools.*`
path (AST scanner rule).

### A13 🟡 — AMEND-T34 (attribute pin + default-deny leg) [reviewer C — closes a fail-OPEN seam]
The new registration pack asserts: (1) `getattr(tool, "_tool_category", None) == "wedge-capture"` on BOTH
constructed tool objects — without it, the name-prefix fallback (`_tool_registry.py:879-882`) infers
`"wedge"` and the strip path FAILS OPEN if the decorator is missing or mis-ordered below `@tool`;
(2) default-deny behavioral leg (empty-allow synthetic agent resolves zero wedge tools) + holders
offenders-scan pinned to exactly `{ari, leader, maintenancer}` (pattern
`test_instance_ops_registration.py:214-224`). (MCP stamping itself is a NON-issue: the
`tool_adapter.py:187-193` rule governs MCP prefix overrides only; native tools take
`@register_tool_category` — the exact self-alarm shape; verified.)

### A14 🟡 — AMEND-T20 (two more router censuses) [reviewer C]
Same file, two additional pins in `tests/unit/test_router_scope_manifest.py`: `include_router` census
`:258-263` **34 → 35**; declared-route census `:306-319` **100 → 104** (pinned literal `:315`; docstring
still says 97 — update both). AUTH-OFF manifest derives automatically (`:148-156`).

### A15 🔴 — AMEND-D43/T36 (3-leg simulation + assertion gaps) [reviewer C + A]
- **Leg (i)** dedicated-thread CPU burn → `sustained_cpu` (concentration co-observed, not asserted);
  negative: `sustained_loop_lag` must NOT fire.
- **Leg (ii)** loop-thread `time.sleep(3)` block → `sustained_loop_lag`; negatives `sustained_cpu` +
  `thread_concentration` must NOT fire — this negative pair IS the H1/H2 discriminator (SC-3's claim is
  currently unproven in the negative direction). Requires per-leg env (CPU threshold high or CPU trigger
  disabled) or the negative is unprovable.
- **Leg (iii) NEW — loop-thread CPU spin (no await), 3–5 s** → `sustained_loop_lag` +
  `thread_concentration` attributed to `loop_thread_native_id`. Proves: watchdog liveness under maximum GIL
  contention, lag semantics during a live wedge, and that a capture COMPLETES on a live wedge — the
  instrument's whole point. (Sleep-based blocks release the GIL and cannot prove this.)
- Assertion gaps to close: (a) **kill-switch leg** (`ENSEMBLE_WEDGE_CAPTURE_ENABLED=0` → no dir, no row,
  `status().enabled == False`) — absent from phase 3 entirely; (b) post-capture watchdog-alive +
  `missed_samples == 0` at boot level; (c) captures identified by `trigger_reason` + injection-window
  timestamp with PER-LEG thresholds (D43's global tiny set makes the daemon's own boot work fire spuriously
  and D44's cooldown proof indeterminate); (d) `lag.json`/manifest carry `last_probe_age_s` + explicit
  `stale`/`never_fired` markers (lag telemetry goes quiet exactly when it matters); (e) transient-unit-gone
  after SIGTERM.

### A16 🟡 — AMEND-T38/D46 (house-trap mappings) [reviewer C]
Add: (1) root `tests/conftest.py` installs langgraph mocks at COLLECTION time before subdirectory conftests
— any pack importing `daemon.api` transitively runs under mocks unless evicted (`tests/support/conftest.py`
is the real-graph fixture home; the wedge_capture package itself is langgraph-free); (2) explicit
`--timeout` on PG-walled legs (v0.27.2 precedent: real-PG legs wall 24.5–26 s vs 30 s ini default);
(3) worktree claim-heartbeat duty (`wt.claim.wedge-capture`); (4) transient-unit-gone check.

### A17 🟡 — AMEND-D51 (seal justification, mechanical) [reviewer C]
Replace the constants.py-only justification with: "diff the full T1–T41 change list against the 35-row
`SEALED_SHA_BASELINES` — ZERO intersection (verified: `checkpoint_perf.py`, `auto_continue_boot_pass.py`,
`api.py`, `_tool_registry.py`, `.env.example`, `docs/**` all absent; constants.py untouched is one row of
that proof, not the proof)."

### A18 🟡 — AMEND-T32/AC46 (boot-line baseline, reproducible form) [reviewer B + architect ruling]
Replace the live "pre-change baseline" count with: static source assertion (number of `[BOOT_CONTINUE]`
logger call SITES unchanged vs base revision) AND runtime assertion that every EMITTED `[BOOT_CONTINUE]`
record carries `mode=` on both passes. The idle-ON-boot vacuous case (no main-pass line) is documented;
the boot RECORD is the primary carrier.

### A19 🟢 — runbook residual-limitation section (AMEND-D36) [reviewer A]
`docs/runbooks/wedge-capture.md` gains a limitations section: the instrument observes **pure-Python**
starvation (the incident's shape); a C extension holding the GIL without releasing starves the watchdog too
(the capture cannot start); `pg_stat_activity` cannot corroborate pool attribution (empty daemon
`application_name` — the note-for-architect stands).

---

## 4. Risk register addendum (renumbered; sources credited)

| ID | Risk | Sev | Mitigation |
|----|------|-----|------------|
| RK-15 | Tick-path logging deadlock (handler lock held by a loop thread blocked on a full stdout pipe) | 🔴 critical | A4 (no logging on the tick path) |
| RK-16 | Lag-trigger silently disabled on a fully starved loop (undefined never-fired semantics; AC4 racy) | 🔴 critical | A1 (D4 a–f + D4b) |
| RK-17 | Fabricated/absent pool numbers in H2 evidence (D21 names don't exist in psycopg_pool 3.3.1) | 🔴 critical | A2 |
| RK-18 | Orphan captures leak past every retention cap (D23 caps computed from the table) — unbounded-growth reprise | 🔴 critical | A3 |
| RK-19 | Pin-set drift: any plan naming a subset of the 8 exact-set sites lands a red tree (T23's "exactly 8 files" makes the wrong commit the SPECIFIED outcome) | 🔴 high | A11 + registry-header rule (grep, never trust counts) |
| RK-20 | New-package import inside the boot-pass envelope silently disables auto-resume (settled-item violation) | 🔴 high | A8 (lazy import + empty `__init__` + pack assertion) |
| RK-21 | One-post-per-tick probe: unbounded ready-queue growth (~1800 queued at a 30-min wedge), burst-on-unblock, racy peak | 🟡 high | A1(b) |
| RK-22 | Tiny-threshold boot smokes false-positive during daemon boot itself; cooldown proof indeterminate | 🟡 med | A15(c) per-leg env + reason/timestamp-qualified captures |
| RK-23 | Lag telemetry goes quiet exactly when it matters (samples stop while wedged) | 🟡 med | A15(d) `last_probe_age_s` + `stale`/`never_fired` |
| RK-24 | Missing/mis-ordered `@register_tool_category` ⇒ name-prefix fallback infers `"wedge"` ⇒ strip FAILS OPEN | 🟡 med | A13 attribute pin |
| RK-25 | "One record, both passes" unimplementable in-body (8 return sites; sweep result discarded at `api.py:2661`) | 🟡 med | A8 call-site write |
| RK-26 | ON boot with 0 candidates emits no main-pass `[BOOT_CONTINUE]` line ⇒ mode invisible in logs | 🟡 med | A8 line-site list; record is primary |
| RK-27 | Capture blocks on DNS/NSS or unbounded connect (connect_timeout covers TCP only) | 🟡 med | A5 resolve-at-start + per-step budgets |
| RK-28 | C-extension GIL holder invisible to the instrument | 🟢 med (residual) | A19 runbook disclosure |
| RK-29 | `=false`/`=off`/`=no` env values mean ON (literal-`"0"`-only disable) — record exposes long-standing surprise | 🟢 low | A8 `raw_env_value` verbatim + `mode_semantics` |
| RK-30 | Two router censuses (34 include_router / 100 declared routes) break loudly on the new mount | 🟢 low | A14 |
| RK-31 | `stage.sh` full-history grep ⇒ `CONTAINS_CONTRACT_PHASE=true` at base; un-flagged stage exits 78 (wasted cycle, no harm) | 🟢 low | S-7 keeps explicit `ENSEMBLE_ROLLBACK_SAFE=1` |

---

## 5. Release-blocking (must land in the plan BEFORE implementation)

1. **A1** — lag-probe semantics (the instrument's core purpose fails silently without it).
2. **A2** — pool-counter mapping (evidence integrity for the H2 hypothesis).
3. **A3** — retention/reconcile/UNIQUE (unbounded-growth reprise).
4. **A8** — boot-record blast radius (auto-resume silently off = settled-item violation).
5. **A11** — 13-file pin ceremony (T23 as written specifies a red tree).
6. **A10** — AC20 rewrite (must precede tester-pack authoring or the lane fails on a pre-existing defect and mis-attributes it).

## 6. Adjudicated decisions (architect rulings on reviewer open questions)

1. **Boot-record write site = the `api.py` call site** (A8). Strictly fewer touches (1 vs 8), both
   `ContinueResult`s + `data_dir` in scope, zero pass control-flow change; the settled "in-body" phrasing is
   superseded as ambiguous. BC-1 preserved via single-read-per-pass carried in the result.
2. **The import blast radius is a HARD release blocker** (RK-20/A8) — lazy import + empty `__init__` +
   import-isolation pack assertion are mandatory, not advisory.
3. **AC46 hybrid approved** (A18) — static call-site count + every-emitted-line-carries-`mode=` runtime
   assertion; vacuous idle-ON case documented.
4. **Shutdown join adopts the house detached form, timeout 5.0 s** (A9) — supersedes the 2.0 s figure that
   appeared in review prompts; the house precedent at `api.py:3246-3262` governs.
5. **D21 vocabulary change is cross-phase-coordinated** (A2) — plan §4.1 ledger, phase2 status surface, and
   the T39 matrix cross-check adopt the amended names in one commit; the reconcile note is recorded, the
   sibling file is not edited.

## 7. Verified clean (no action)

- **GIL reality:** watchdog wake latency under a pure-Python spin = median 6.28 ms (measured, CPython
  3.12.3; re-check on 3.13 at impl time) — the daemon-thread design is sound.
- **`faulthandler` cross-thread:** 0.22 ms all-threads dump while another thread spins; spinner frame
  present; needs fileno-bearing file; exceptions swallowable. Measured.
- **One-shot connection independence:** no engine lock, no prepared-statement collisions (per-session;
  `prepare_threshold=5` never reached on a one-shot), max_connections headroom ≈ daemon demand (5 pool +
  5 asyncpg + engine pool) + 1 — record the live value in the runbook.
- **`readiness_composite`:** published by replacement (lock-free State) — safe cross-thread (with A6's
  tz-awareness caveat).
- **Pause/cancel:** zero interaction (no `threading` in `instance_lifecycle.py`; `graph_task.cancel` is an
  asyncio cancel; watchdog uses no executor).
- **SIGTERM-only discipline:** no `signal.signal`/`atexit` anywhere in `daemon/*.py` + `daemon/services/*` —
  nothing the plan adds changes that.
- **MCP privilege stamping:** non-issue for native tools (governs MCP prefix overrides only; native path is
  the `@register_tool_category` decorator — exact self-alarm shape).
- **meta.json / prompt artifacts:** `tools.allow` is the key; 161-row baseline has 0 meta.json rows.
- **Migration prefix:** `20261022_000002` next-safe at `639b7129b` (108 files, all distinct — verified);
  D50's stage-time re-derive stays (concurrent lanes).
- **D49 rollback adjudication:** all four legs verified (git grep 0 hits; PG runner structural no-op;
  `rollback_migration()` zero production callers; inert-post-rollback) ⇒ `rollback_safe=true
  source=explicit` — the v0.27.2 STANDING RULE citation is correct.
- **Forbidden surfaces + seal:** 0/35 intersection, zero forbidden-path touches, no
  `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` affirmative use anywhere in the plan.

## 8. Unverified at plan time (impl-time commands)

| Item | Command | Owner |
|---|---|---|
| `get_stats()` keys at runtime | `python -c "import psycopg_pool,inspect;print(psycopg_pool.__version__);import psycopg_pool.pool_async as p;print(inspect.getsource(p.AsyncConnectionPool._get_measures))"` — pin the A2 mapping in the pack | P1/P3 |
| faulthandler output on CPython 3.13 | `python3.13 -c "import faulthandler,tempfile;f=tempfile.TemporaryFile();faulthandler.dump_traceback(file=f,all_threads=True);f.seek(0);print(f.read().decode())"` | P3 |
| PG `max_connections` headroom on live host | `psql -c 'show max_connections'` — record in runbook; require ≥ 2× daemon demand + 1 | P1 |
| `native_id` ↔ tid mapping (zero tree precedent) | capture records BOTH `native_id` and `comm` (D6) — pin the mapping in the P3 pack | P3 |

## 9. Confidence

**High.** Three independent reviewers, each grounded in the actual tree at `639b7129b`, with empirical
measurements where the question was mechanical (GIL wake latency, faulthandler, sqlite boot failure) and
two independent convergences (`UNIQUE(capture_dir)`; lag-staleness markers). The recommendation flips only
if: (a) `psycopg_pool` is upgraded past 3.3.1 before implementation (mapping re-verified by the pinned
pack — it fails loudly), or (b) a concurrent lane takes `20261022_000002` (D50's re-derive covers it), or
(c) the privileged pin set drifts again before the ceremony commit (A11's table is re-derived by grep at
ceremony time per the registry header's own instruction).

**Gaps:** none — all three dispatched reviewers reported; one reviewer was a single re-dispatch after a
provider model deprecation (first attempt produced no report; nothing from it is relied upon).
