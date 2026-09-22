# Changelog

All notable changes to the agents-ensemble project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — 2026-09-22

### Changed

**`ENSEMBLE_SELF_ENV` is now OPTIONAL with explicit opt-out** (D-FA2.3 supersession — user directive 2026-09-22: "remove the ensemble self env, feature active by default... default value, if user want disable they can set to false"). The marker stays the highest-priority source; absent + no opt-out + unambiguous launch evidence auto-derives the env.

- **Auto-derive signal hierarchy** (multi-signal — the single-signal PORT-derivation D-FA2.3 rejected cannot recur):
  1. **Explicit `ENSEMBLE_SELF_ENV` marker wins** (`dev`/`demo`/`live`/`sandbox`) — staged by `stage.sh` into `INSTALL_DIR/.env`, exported by `launcher.sh` `load_env_file`. Unchanged: the marker is consumed FIRST by `_self_env_marker`.
  2. **Explicit opt-out → today's fail-closed behavior preserved verbatim.** `ENSEMBLE_SELF_ENV=0|false|no|off` (case-insensitive, whitespace-trimmed — mirrors `daemon.config._PROACTIVE_FALSE_BOOLS` vocabulary used by `ENSEMBLE_EMPTY_RESPONSE_GUARD` etc.) returns `None`; actor tools refuse `env-marker-absent`; read tools accept only omitted target or `target_env=dev`. Operators who want the strict old contract get it byte-for-byte.
  3. **Auto-derive when marker absent and no opt-out.** Frozen-binary + `releases/` for sandbox (binary lives at `<INSTALL_DIR>/releases/<ver>/ensemble-prod` per `stage.sh:5-17`; install dir is `exe.parent.parent.parent`); install-dir + POSTGRES_DB cross-check for `live`/`demo` (canonical `~/agents-ensemble/.env` / `~/agents-ensemble-demo/.env` must contain `POSTGRES_DB=ensemble_prod` / `ensemble_demo` OR the daemon's own `$POSTGRES_DB` must match); dev-shape `POSTGRES_DB=ensemble_dev` for `dev`.
- **Resolver observability** — `_self_env_source()` returns `explicit` / `opted-out` / `auto` / `auto-unresolved` / `ignored-garbage`; `release_info` `env-marker:` line reports the path that produced the resolution so a misconfiguration is visible immediately (auto-derived envs are NOT silently attributed to the staged marker; garbage markers are NOT silently printed as "ABSENT" — they show the IGNORED raw value with a WARNING in the daemon log).

### Fixed

- **Sandbox install-dir resolution** (pre-existing bug in `_resolve_install_dir`): the frozen-binary layout puts the binary at `<INSTALL_DIR>/releases/<ver>/ensemble-prod`, so the install dir is `exe.parent.parent.parent` (TWO `.parent`s up). The pre-fix code used `.parent.parent` which resolved to `<INSTALL_DIR>/releases` and never matched `releases/`, so sandbox install-dir resolution was always returning `None`. The new auto-derive test (`test_auto_derive_sandbox_via_frozen_binary`) caught it; the helper now resolves correctly and the same fix unblocks the post-P2.1 install.
- **Garbage-marker silent fall-through** (W1 — reviewer APPROVED-WITH-NOTES commissioned item): pre-fix, `ENSEMBLE_SELF_ENV=<unrecognized value>` (e.g. `prod`, `flase`, `Live`) silently fell through to auto-derivation AND the `env-marker:` report line printed `ENSEMBLE_SELF_ENV ABSENT` — factually wrong (the marker WAS set, just ignored). The new code classifies the marker into the `ignored-garbage` source state, logs a WARNING with the raw ignored value, and the `env-marker:` line prints the IGNORED raw value (so the operator sees their typo). **Case-sensitivity decision:** the explicit enum match (`_explicit_marker`) is case-sensitive (a deliberate non-normalizing exact enum match — `Live` / `LIVE` are garbage, not `live`); the opt-out match (`_is_opt_out_marker`) is case-insensitive (mirrors `daemon.config._PROACTIVE_FALSE_BOOLS`); the new garbage detection uses the explicit-enum case-sensitive comparison (so wrong-case values are detected as garbage, not silently coerced).
- **`env-marker-absent` refusal text now accurate per source state** (S1 — reviewer commissioned): pre-fix, the refusal text said "no opt-out via false" even when the operator DID opt out, and gave the same remediation hint for garbage-marker and no-signal cases. The refusal hint is now branched on `_self_env_source()`: `opted-out` points the operator at removing the opt-out; `ignored-garbage` points them at valid forms / opt-out / unset; `auto-unresolved` points them at explicit marker or unambiguous launch evidence. The fail-closed contract (refusal token `env-marker-absent`) is unchanged.

### Safety invariants (unchanged)

- **Auto-resolution changes identity DETECTION, NEVER the gates.** Auto-resolved `live` still requires the 3-factor confirmation gate (param + user-origin window + nonce content match) before any live mutation. Auto-resolved `live` `system_restart` is still refused outright (A2/§3.1). `env-self-match` semantics are unchanged. Explicit opt-out (`ENSEMBLE_SELF_ENV=0|false|no|off`) preserves today's full fail-closed refusal for actor tools.

### Operator migration

- **No action required for current installs.** Daemons with a staged marker continue to read it (highest priority). Daemons without one (e.g. the LIVE daemon at `~/agents-ensemble`, port 9797, which pre-dates P2.1) now auto-derive `live` via install-dir + POSTGRES_DB cross-check.
- **Explicit opt-out** — set `ENSEMBLE_SELF_ENV=false` (or `0`/`no`/`off`) in `INSTALL_DIR/.env` if you want the strict old contract.
- **Multi-install hosts (operator callout — W2).** On hosts running multiple installs side-by-side (e.g. a dev checkout + demo + live on one host), a dev daemon that picks up ambient `POSTGRES_DB=ensemble_prod` from the shell environment OR a leftover `~/agents-ensemble/.env` from a prior install will now auto-derive `live` — fail-closed in the sense that all gates apply (env-self-match, live 3-factor, live restart refusal), but the resolved env IS `live`, not `dev`. The `release_info` `env-marker:` line reports the auto-derived path so a misconfiguration is visible. If you maintain multiple installs on one host and want a particular checkout to stay `dev`, set an explicit `ENSEMBLE_SELF_ENV=dev` in the daemon's environment (or its `.env` file), OR opt out entirely via `ENSEMBLE_SELF_ENV=false`. The same applies to garbage / typo'd marker values (`prod`, `flase`, `Live`): the value is silently IGNORED (a WARNING is logged with the raw value) and the resolver falls through to auto-derivation — if you meant `live` you probably got `live`; if you meant something else, the warning is the operator-visible signal that the marker was not honored.

### Tests

- **New** — `TestAutoResolution` class in `tests/unit/tools/test_upgrade_tools.py` (19 cases): explicit-wins, opt-out vocabulary (`0`/`false`/`no`/`off` / case-insensitive / whitespace), auto-derive per env shape (live via install-dir topology, live via ambient POSTGRES_DB, demo, dev, sandbox via frozen binary, sandbox falls back when install matches live/demo, unresolved when no signal, ignores unrelated POSTGRES_DB), `release_info` shows auto-resolved env without marker, auto-resolved live still requires 3-factor gate, auto-resolved live refuses restart outright, auto-derived env-self-match refuses cross-env.
- **New** — W1 garbage-marker tests in `TestAutoResolution` (5 cases + 4 parametrized): garbage + auto-derive succeeds, garbage + no signal logs WARNING, marker-line reports IGNORED raw value (resolved and unresolved cases), garbage detection covers typos / wrong-case / punct-spoofs (`prod`, `flase`, `production`, `demo;live`).
- **New** — S1 refusal-text accuracy tests (3 cases): operator opt-out, garbage marker, no-signal — each refusal hints the right remediation; the old "no opt-out via false" wording is gone.
- **New** — S2 vocabulary pin: `test_opt_out_falses_pinned_to_proactive_false_bools` asserts `set(_OPT_OUT_FALSES) == set(daemon.config._PROACTIVE_FALSE_BOOLS)` so drift between the two vocabularies is caught.
- **New** — S3 armed-spoof gate proof: `test_fabricated_nonce_with_no_window_refuses` — fabricated nonce string + `user_confirmed=True` + NO genuine user-origin window → REFUSED with `user-confirmation-missing`. Pinned the existing `test_auto_resolved_live_still_requires_three_factor_gate` to the concrete token (`CONFIRMATION REQUIRED (live)` for dry_run; `user-confirmation-missing` for armed without window) — the old `REFUSED in out or CONFIRMATION in out` weak assertion is replaced.
- **New** — S4 edge tests (6 parametrized): empty-string / whitespace-only (`" "`, `"\t"`, `"\n"`, mixed whitespace) marker → `absent` (not garbage), resolver source is `auto` / `auto-unresolved` (never `ignored-garbage`).
- **New** — S5 parametrized explicit-wins (4 cases): explicit marker wins over install-dir evidence for a DIFFERENT env (one case per valid marker value).
- **Updated** — `test_marker_absent_reads_fail_open`, `test_env_marker_absent_actor_fail_closed`, `test_spoofed_env_marker_unresolved` now use the new `unresolved_env` fixture (silences every signal the resolver reads — `ENSEMBLE_SELF_ENV`, `POSTGRES_DB`, `Path.home()`, `sys.frozen`) so the OLD contract (marker absent → fail-closed) is exercised cleanly under the NEW behavior (auto-derive fills in when signals are present).
- **Helper** — `unresolved_env` fixture (modulelevel, in `test_upgrade_tools.py`) and `_read_env_value` shell-style .env parser (in `daemon/tools/upgrade_tools.py`) shared with future test additions.

---

## [0.13.10] — 2026-09-22

### Fixed

Fixes the job-completed result arm on the true lineage (`fix/job-completed-result-arm`, `ae9264dc..93804b1b` + ship-prep round). The v0.13.9 cherry-pick fixed the premature-terminal arm, but its result arm was stale-master intent that was never wired on the post-Phase-5 pipeline — where the JobItem mirror columns are gone and `Task.result` is the durable home for the agent's last response (re-confirmed live on demo 2026-09-21/22: `result_summary=null`, zero `job_completed` rows).

- **Task.result content stamping** — `ProcessMessageProcessor.on_success` stamps `content: result.result_content` into the `complete_task` payload (producer side of the pipeline).
- **Resolver surfacing** — the dual-backed resolver (`work_resolver._job_to_record`) surfaces `Task.result` via `_parse_task_result_summary`, so `GET /api/jobs/{id}` and the SSE `event: completed` payload carry the agent's response instead of null.
- **EventKind.JOB_COMPLETED + persistence + notification kwarg** — new event kind; observer sibling publish (per-JobItem, fired outside the `instance_was_terminal` gate so shared-instance JobItems each get a row); `result_summary` threaded through publisher → broadcaster so `/api/notifications/stream` frames carry it.
- **ERROR-branch extraction restoration** — failed terminals extract best-effort result content again.
- **Intent5 emission-surface regression test** — real-daemon test asserting all four emission surfaces (SSE payload, GET body, persisted `job_completed` event row, notifications), strengthened (F6a) to assert truthy `content` inside the `result_summary` envelope.
- **Review-hardening (F1/F5/F6a)** — F1 env foot-gun guard: the e2e refuses to run when the resolved `POSTGRES_DB` is prod-like (`ensemble_prod`/`ensemble_live`/`ensemble_demo`) without an explicit `E2E_PG_DB` override — the exact ambient-fallback pattern that caused the 2026-09-21 live-DB incident. F5 acceptance-pack Intent5 gating: Intent5 failure with a daemon available ⇒ pack FAIL; no-daemon skip ⇒ LOUD `PASS-WITH-SKIP` verdict instead of a silent PASS. F6a envelope content assertions.

---

## [0.13.9] — 2026-09-21

### Fixed

Cherry-picked from `fix/empty-job-completed-event` (round-1 + round-2 + round-3 council fixes) onto the `release/prepare-v0.13.9` lineage (`origin/latest` @ `ea6a3944`, plus the four round-1/2/3 cherry-picks). job-completed events now carry Result body; result_summary written at completion; premature terminal emission gated on true subtree completion; failed/dead-letter events carry Error body — event-driven, no polling.

> **⚠ Superseded by [0.13.10] (2026-09-22):** the claim "job-completed events now carry Result body; result_summary written at completion" described stale-master (`fix/empty-job-completed-event`) intent and was **never true on this lineage** — the post-Phase-5 pipeline (JobItem mirror columns dropped, `Task.result` as the durable home) was never re-wired. Re-confirmed live on demo 2026-09-21/22: `result_summary=null`, no `job_completed` event rows. 0.13.10 fixes the result arm on the true lineage. The premature-terminal gating and failed/dead-letter Error-body claims from this entry WERE live and are unaffected.

---

## [Unreleased] — 2026-09-06

> **⚠ Operator callout — `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` default flipped
> OFF → ON.** If you were relying on the previous "unset = OFF" posture as your
> revert path, that is no longer the case: an unset (or blank) env now resolves
> to ON, and the bounded unstick runs by default. **Set
> `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0` (also accepts `00` / `-0` / `0.` /
> `0x0` / `false` / `no` / `off`) *before restarting* the daemon** to land the
> previous default behavior. Restart is required — the resolver is cached at
> boot (`daemon/services/job_recovery_service.py::_resolve_defer_autopromote_enabled`).
> Census unchanged (23/1/0): the unstick writes `task.is_deferred` only.

### Fixed — Unblock round (`fix/defer-self-witness-and-cleanup`)

#### Preflight defer-count wiring (ITEM 1)

The preflight endpoint (`GET /api/jobs/cleanup/preflight`) previously
read `manager._defer_block_resolver`, but `daemon/api.py:977-978`
wires **only** the `daemon.routers.queues` module-global singleton
(`set_defer_block_resolver(DeferBlockResolver(...))` is the
production-shape wiring). `manager._defer_block_resolver` was NEVER
assigned in production, so `defer_blocked_count` silently stayed `0`.
The preflight now consumes the already-wired singleton via
`daemon.routers.queues.get_defer_block_resolver()` — the same
factory the `GET /api/queues/defer-blocked` endpoint uses — so the
defer count surface and the resolver singleton can no longer drift.

The unit test that hand-set `manager._defer_block_resolver` (the
round-2 mask class that hid the gap) was updated to use
`set_defer_block_resolver(resolver)` and now exercises the
real-wiring path end-to-end.

#### `defer_pending_count` is an instance method (ITEM 4)

`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count()`
is now a public INSTANCE method (round-2's free function
`defer_pending_count(engine)` is gone). The engine is reached via
`self._job_repo.engine` internally — no direct
`_job_repo.engine` reach-through from the router anywhere. The SQL
constant `_DEFER_PENDING_COUNT_SQL` stays module-private (underscore
prefix).

#### Cycle docstring rewritten to truth (ITEM 3)

The `jobs_management.py` docstring claimed a `schemas → daemon.routers`
edge that does not exist. The real traced cycle goes through the
routers package boundary:

```
jobs_management → defer_block_resolver → routers.schemas →
  routers/__init__ → queues.py:32 → recursion
```

The deferred-import pattern stays (the cycle is real); the docstring
now describes the actual edges.

#### Canonical copy cross-surface pin (ITEM 5)

The cleanup-truth-split sentence (Round-2 ITEM 3 / T-H1) now lives
on three surfaces, each pinned by a plain-TS or Python spec:

* **BE**: `daemon/routers/jobs_management.py:cleanup_preflight`
  docstring;
* **FE**: `frontend/src/app/models/cleanup-preflight.model.ts` —
  exported constant `CLEANUP_TRUTH_SPLIT_COPY` referenced by
  `system-cleanup-confirm-dialog.component.ts`;
* **docs**: `docs/job-task-system.md` §8.5.

A drift between any two surfaces breaks `cleanup-preflight.model.spec.ts`
(plain-TS, no Angular TestBed) or `tests/unit/test_docs_cleanup_truth_split.py`.

### Known follow-ups (ledgered for visibility)

* **`TestIdlePredicatePgSqliteParity` boolean-bind helpers** —
  `tests/job_queue/test_defer_gate_post_settle_window.py` methods
  `_insert_queue` / `_insert_job` bind integer literals `1,0` for
  the boolean-typed columns (`is_system`, `is_paused`,
  `concurrency_limit`-as-flag). SQLite accepts the bind; PostgreSQL
  rejects it (`InvalidTextRepresentation` on the boolean type). The
  parity test currently runs red-on-PG until the helpers are made
  dialect-aware (e.g. use `True/False` and let SQLAlchemy coerce, or
  branch on `engine.dialect.name`). Tracked for the next round —
  not blocking the unblock.

---

### Fixed — WS4 Round-2 (`fix/defer-self-witness-and-cleanup`)

#### Force-complete TOCTOU re-check (Round-2 W1)

`JobQueueService.force_complete_defer_holder` now re-derives the
`has_live_work(instance_id)` predicate IMMEDIATELY before
`terminate_instance`, in addition to the original probe at the top
of the method. A small probe→terminate window remains; the second
call catches state that lands between the probe and the destructive
call (delegating-repo write, injected state). A busy re-check
returns `terminated=False, probe_busy=True` (200, NOT an exception).
The docstring no longer claims the guard is "race-proof" — the
remaining window is covered by `terminate_instance`'s own
idempotency-on-terminal cascade.

#### Holder-probe scope gap — task + child-instance arms folded in (Round-2 W2)

The original holder probe
(`JobRepository.has_active_non_deferred_work(None, requester_instance_id=<holder>)`)
was job-side only. It missed two live-work shapes the bulk zombie
scan already detected:

* a Task in `pending`/`running`/`paused` (no JobItem at all —
  direct Task, common for forked helpers / reaper sweep);
* a non-terminal child instance (a `waiting_children` parent whose
  subtree is still executing).

A new `SQLModelInstanceRepository.has_live_work(instance_id)`
single-instance companion reuses the same three CSV constants the
bulk `_build_zombie_scan_sql` bakes into the zombie predicate
(`_TERMINAL_STATUSES_FOR_ZOMBIE_SCAN`,
`_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN`,
`_LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN`) — derive-don't-reimplement.
`force_complete_defer_holder` now uses this companion for both the
initial probe and the W1 re-check; the predicate arms cannot drift
from the bulk scan. A holder with a live Task (no JobItem) OR with
a non-terminal child is now refused with
`terminated=False, probe_busy=True`.

### Changed

#### Preflight copy truth (Round-2 ITEM 3 / T-H1)

The cleanup preflight docstring replaces "Live missions will remain"
with the canonical split sentence (FE const + docs §8.5 use the
same — cap-exception micro-round ITEM 2, 2026-09-06 broadens to
"settled mirrors, or running Tasks without JobItems" so the
sentence matches the truth-survivor filter's TRUE semantics):

> Every ACTIVE job is cancelled, together with its whole subtree.
> Only missions holding settled mirrors, or running Tasks without
> JobItems — are kept.

Term single-owner: "stalled mission" (operator-facing). The
`zombie_instance_count` wire field NAME stays technical (wire
stability).

#### Operator vocabulary (Round-2 ITEM 8)

The cleanup endpoint's router docstring replaces "nuclear press"
with "System Cleanup" (the operator-facing button label). The
holder-action term is "stalled mission". The technical wire fields
(`zombie_instance_count`, `live_instance_count`, etc.) STAY
unchanged.

### Added

#### Mission tree panel API surface (2026-09-07, `feature/job-queue-mission-tree`)

`GET /api/missions` / `GET /api/missions/{id}` carry two additive nullable display
fields sourced from `instance_metadata`: `title` (string-stripped; wire-bounded at
500 chars) and `initiative_preview` (whitespace-collapsed FIRST, THEN truncated to
140 chars — `INITIATIVE_PREVIEW_MAX_CHARS` — by a plain slice, no ellipsis). Honest
nulls only: `null` when the key is absent, the value is non-string (the metadata JSON
column is untyped), or it collapses to empty — the server never fabricates a fallback
label and never stringifies (`str(123)` is forbidden).

`GET /api/jobs` gains a `mission_id` filter (`mission_id == instance_id`; narrows
`job_queue_items` by `JobItem.instance_id` on the EXISTING count + page queries — no
extra SELECT, count/page symmetric) plus a **deprecated** `instance_id` alias (used
only when `mission_id` is absent; OpenAPI `deprecated: true`). Empty-string
semantics (second-pass review fold): the primary `mission_id` rejects empty with
**422** (`min_length=1`); the alias accepts empty and means **filter-by-empty**
(200 with an empty page, `total == 0`) — NOT "no filter". The same `is not None`
semantics hold at the service and repository layers for direct callers. Unknown
mission ⇒ 200 empty page. Read-only throughout — zero DML, census frozen at 23.
Full contract: `docs/job-task-system.md` §8.6.

#### Public `defer_pending_count` surface (Round-2 ITEM 7 → unblock-round ITEM 4)

Originally a public free function
`daemon.services.defer_block_resolver.defer_pending_count(engine)`;
replaced in the unblock round by the public instance method
`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count()`.
The preflight endpoint (`GET /api/jobs/cleanup/preflight`) calls
the instance method via the resolver singleton — NO direct
`_job_repo.engine` reach-through from the router anywhere.
Schema or shape changes to the defer-pending-count SELECT have ONE
place to update (the SQL constant `_DEFER_PENDING_COUNT_SQL`,
which stays module-private — underscore-prefixed, not re-exported).

#### Pattern-(g) defer-job watchdog + `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`

`Pattern-(g)` is the JOB-SIDE watchdog complement to the
task-side `Pattern-(a)` recovery
(`daemon/services/job_recovery_service.py`). Pattern-(g) covers
stuck JobItems (stuck-active jobs with dead instances,
stuck-queued jobs behind dead instances) — the job-queue
lifecycle. Pattern-(a) covers stuck Tasks — the task lifecycle.
The two are complementary: a stuck JobItem with a healthy Task is
a Pattern-(g) job; a stuck Task with a settled JobItem is a
Pattern-(a) task. Pattern-(g) does NOT inspect Task state;
Pattern-(a) does NOT inspect JobItem state.

The `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` env var (default ON,
env-only, restart-read) gates Pattern-(g)'s auto-promotion path.
Default ON is the new posture (operator decision 2026-09-06 after
soak): the bounded unstick runs by default on every drift sweep.
Operators who need to revert (or are mid-incident on this path) set
`ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0` (also accepts `false` / `off`)
+ restart; the explicit-OFF escape hatch is byte-identical to the
previous default behaviour (WARN only, no `task.is_deferred` writes).

### Notes — boot-log string change (operator grep alert migration)

The one-shot boot INFO emitted by `emit_defer_autopromote_boot_log`
(`daemon/services/job_recovery_service.py`) swapped its default-tag
prefix in tandem with the default flip:

* **Old (default-OFF posture)**: DISABLED state led with
  `"Pattern-g defer-self-witness autopromote DISABLED (default; %s unset or set to a falsy value) — ..."`,
  ENABLED state led with `"... ENABLED (operator opted-out via ...)"`
  (rare on this path).
* **New (default-ON posture)**: ENABLED state leads with
  `"Pattern-g defer-self-witness autopromote ENABLED (default; %s unset or set to a truthy value) — ..."`,
  DISABLED state leads with
  `"... DISABLED (operator opted-out via %s=0/00/-0/0./0x0/false/no/off) — ..."`.

**Operators with grep-based alert rules keyed on the old
`DISABLED (default; …)` prefix must update those rules** — without
the migration, the rule will go silent on a deployed instance where
the kill-switch is in its explicit-OFF escape-hatch posture. The new
substring is `DISABLED (operator opted-out via …)` (the path stays
identifiable via the leading `Pattern-g defer-self-witness autopromote`
token in either state). Boot-log emission cadence is unchanged: exactly
one INFO line per process; restart required to re-evaluate the resolver.

---

## [Phase 8] — Cleanup old architecture (FINAL)

The final cleanup phase. The `use_dependency_bus` feature flag and the `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` env var have been removed; the DependencyBus is now the SOLE completion authority with no flag, no kill-switch, and no fallback path.

### Removed

- **`USE_DEPENDENCY_BUS` flag** — `use_dependency_bus` field removed from `JobSystemConfig` (`daemon/config.py`). The `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` env var is no longer read. The bus path is now unconditional; all `if use_dep_bus:` / `_is_dependency_bus_enabled()` conditionals have been removed. The DependencyBus is the only completion authority for parent-waits-for-children; no rollback path is supported.

---

## [Unreleased] — 2026-06-21

### Phase D: Dependency Bus & Cleanup (M7 + M8)

The final phase of the decouple architecture migration. The system now has a single dispatcher, a single scheduling layer, and a single DB-backed completion authority. The CorrelationManager is retained as a rollback path; the `waiting_for` / `children` / `instance_hierarchy` artifacts are dead-but-present pending a manual migration.

### Changed

- **Source adapter default agent**: `default_agent` for Slack and Telegram source adapters changed from `"leader"` to `"ari"`. The `ari` agent is the designated chat-source front door (has `job` tool + `job-orchestration` skill). Existing deployments relying on the implicit `leader` default will now route chat messages to `ari` instead. Operators who need `leader` can set `default_agent: "leader"` explicitly in the source config.
- **Reasoning echo flipped from allowlist to denylist**: `reasoning_content` is now echoed back in multi-turn assistant messages for every model by default (previously a `deepseek`-only allowlist). Operators on endpoints that reject the extra `reasoning_content` field (e.g. the raw OpenAI API returning 400 on unknown fields) should set `OPENAI_REASONING_ECHO_DISABLED_MODELS=gpt-4o,claude` (example values) to opt those models out. The old `OPENAI_REASONING_ECHO_MODELS` env var is no longer read; leaving it set logs a deprecation warning at startup pointing to the new key.
- **`OPENAI_ALLOWED_MODELS` renamed to `OPENAI_SELECTABLE_MODELS`** (soft deprecation): the env var governing the spawn-time model allowlist has been renamed. The new primary name is `OPENAI_SELECTABLE_MODELS`; the legacy `OPENAI_ALLOWED_MODELS` is still honored when the new name is unset, but a one-shot deprecation warning is logged at startup. The internal config field (`config.llm.allowed_models`) and the governor `<allowed_models>` prompt block name are unchanged — only the env-var-level aliasing changed. **Operator action**: rename the env var in your `.env` / launcher exports from `OPENAI_ALLOWED_MODELS` to `OPENAI_SELECTABLE_MODELS` to silence the warning. The allowlist is consulted only by the four spawn-time selection flows (spawn `model=` override, weighted `llm_models` pool filter, `spawn_councilor` validation, session-restore re-validation); purpose-bound models (`model_title`, `model_keywords` when set to a fixed value, `model_vision`, compaction, skill evolution) are unaffected and continue to use their own env vars / YAML keys.
- **Empty / whitespace-only env values behave as unset** for both `OPENAI_SELECTABLE_MODELS` and `OPENAI_ALLOWED_MODELS` (legacy shell-style `:-` semantics preserved). A bare `KEY=` line in `.env` — which `launcher.sh` `load_env_file` exports verbatim — produces the documented default (`["agentic", "coding"]`) rather than an empty/unrestricted allowlist, and never fires a spurious deprecation warning. **There is no env-var path to unrestricted mode**; operators who want to lift restrictions entirely must hardcode `allowed_models: []` in `config.yaml`.

### Fixed

- **TOCTOU race in `job_create` watch registration**: When `watch=True`, the watcher is now registered BEFORE the job is enqueued, closing a race window where fast jobs could complete before the watcher was registered (causing missed `[JOB_EVENT]` notifications).
- **`job_continue` crash with `USE_WORKER_POOL=false`**: Direct `manager._task_repo` attribute access replaced with defensive `getattr` pattern.
- **`watch_job`/`watch_jobs` missing error/result context**: Terminal job notifications now pass `result_summary` explicitly so the downstream resolver can fill gaps.
- **`ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` default flipped OFF → ON** (operator decision 2026-09-06 after soak). The Pattern-(g) defer-self-witness watchdog's bounded unstick (`_resolve_defer_autopromote_enabled` in `daemon/services/job_recovery_service.py`) now runs by default — an unset env var, a blank value, and unparseable non-blank values all resolve to ON. The explicit-OFF escape hatch is byte-identical to the previous default: set `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0` (also accepts `false` / `off` / `no`) and restart. The default-OFF path still emits only the WARN and writes nothing — the `OFF=no-writes` contract is unchanged. Census unchanged (23/1/0): the unstick writes `task.is_deferred` only, which is out of the admission-state writer scope. Boot-log copy (`emit_defer_autopromote_boot_log`) updated to state the new default and the explicit-OFF escape hatch.

### Added

- **DependencyBus service** (`daemon/services/dependency_bus.py`) — new authoritative parent-waits-for-children mechanism. DB-backed via the `dependency_watchers` table; watcher state survives restart by construction (no `rebuild_from_db` hack). Public API: `watch(source_task_id, FollowUp)`, `emit_terminal(task_id, Outcome)`, `cancel_for_target(target_instance_id)`. The `start()` method warms the in-memory cache from the DB and recovers FIRED-but-unsent rows for crash safety.
- **`dependency_watchers` table** (`daemon/repositories/dependency_bus/`) — durable storage for in-flight parent→child correlation. Keyed by `source_task_id` for O(1) terminal-emit lookup. Columns: `watch_id`, `source_task_id`, `target_instance_id`, `follow_up_payload` (JSON), `metadata` (JSON), `created_at`, `fired_at` (nullable), `state` (PENDING / FIRED / CANCELLED). Uses the `WriteGuardSession` pattern.
- **`use_dependency_bus` feature flag** (default `True`) — toggles between the DependencyBus (authoritative) and the CorrelationManager (rollback path). `use_dependency_bus=False` reverts to the proven in-memory CM path with no code change.
- **`completion_delivery_path=cm|bus` structured log metric** — every terminal emit writes this key, letting operators verify which authority is in effect per request.
- **30-test Dependency Bus test pack** (`tests/test_dependency_bus.py`, 25 SQLite + 5 PostgreSQL in `tests/postgres/test_dependency_bus_pg.py`) — proves the bus eliminates the double-decrement bug, survives restart, enforces backpressure, and that `cancel_for_target` prevents orphan FollowUps.
- **Pause pre-check in `JobProcessor.start_job`** — instance pause is now a pre-check before admitting a job (replaces the historical `MessageJobHandler.handle()` pause-vs-terminate discrimination).

### Changed

- **`use_dependency_bus` default flipped to `True`** — the Dependency Bus is the source of truth for parent-child correlation. The CorrelationManager is no longer on the hot path.
- **Single execution path** — WorkerPool is the sole execution layer for all work (messages, tasks, completion reports, error reports). The JobQueue is now scheduling vocabulary only (priority, queue management, project scoping for `Task` rows).
- **Pause semantics** — `pause_instance_cascade()` now calls `dependency_bus.cancel_for_target()` on the paused root, cancelling any in-flight FollowUps that would otherwise land on a paused parent. `terminate_instance()` calls the same on the terminated node (after the DB cascade + lifecycle event publish).
- **Completion delivery via DependencyBus** — child completions and error reports now flow through `dependency_bus.emit_terminal()` (gated behind `use_dependency_bus`, called from `child_reports.py` / `error_reporting.py`) instead of `CM.resolve_response()`. The `MessageProcessingPipeline` still owns the shared six-stage flow but delegates the terminal emit to those services — no separate stage 5 hook.
- **Documentation** — `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, and `docs/architecture.md` updated to reflect the new architecture (single dispatcher, DependencyBus, removed dual-path, dead-but-present columns).

### Removed

- **`MessageJobHandler` (770 lines, `daemon/services/message_job_handler.py`)** — deleted in D12. The MESSAGE-dispatch branch in `JobProcessor` is also gone (D11). Pause-vs-terminate discrimination has moved to a pre-check in `JobProcessor.start_job`.
- **MESSAGE-specific helpers in `JobQueueService`** (`daemon/services/job_queue_service.py`) — removed in D13. The JobQueue no longer owns a `JobItem` lifecycle for messages; only `Task` rows are written.
- **`job_type='message'` JobItem rows** — no longer written. `JobItem` rows now exist only for non-message work (scheduler, webhook, project-rooted tasks).

### Deprecated

- **`Instance.waiting_for`** — dead-but-present column. Was the legacy control-flow counter (ADR-011), then became a rebuild-only cache for the CM. Post-Phase-D, completion flows through the DependencyBus. Pending drop via `20260621_000002_drop_legacy_completion_columns.sql`.
- **`Instance.children`** (denormalized JSON array) — dead-but-present cache. Pending drop via the same migration.
- **`instance_hierarchy` table** — dead-but-present. The hierarchy is encoded in `Instance.parent_id` and the live `dependency_watchers` rows. Pending drop via the same migration.

### Migration

- **`20260621_000002_drop_legacy_completion_columns.sql`** (new, IRREVERSIBLE, **not auto-applied**) — drops `Instance.waiting_for`, `Instance.children`, and the `instance_hierarchy` table. Manual application required after **2+ weeks of clean bus operation** in production. Operators should drain in-flight jobs before applying.

### Rollback

- **`use_dependency_bus=False`** — reverts to the CorrelationManager (in-memory `_pending` + per-parent `asyncio.Lock`) as the completion authority. No code change required; the flag is a kill switch. The CM was the proven completion mechanism for the previous release.
- **`use_legacy_waiting_for_cascade=True`** — re-enables the legacy `waiting_for` SQL cascade and the `SELECT COUNT(*)` fallback (defensive last-resort). Useful only if the bus AND the CM both fail in production.
- **`debug_completion_invariant=True`** — keeps the CM tracking in parallel with the bus and logs divergence between CM pending counts and `dependency_watchers` rows. Observability safety net for one more release.

### Migration map: which call sites changed

| Site | Before (Phase C) | After (Phase D) |
|------|------------------|-----------------|
| `daemon/services/message_processing_pipeline.py` | `CM.resolve_response()` not present (CM hook fired by `child_reports`) | unchanged — pipeline delegates to `child_reports` / `error_reporting` |
| `daemon/tools/instance.py` `send_message` | `notify_corr_register` (CM hook) | `dependency_bus.watch(FollowUp)` (under flag) |
| `daemon/services/child_reports.py` | `notify_corr_resolve` (CM hook) | `dependency_bus.emit_terminal()` (under flag) |
| `daemon/services/error_reporting.py` | `notify_corr_resolve` (CM hook) | `dependency_bus.emit_terminal()` (under flag) |
| `daemon/services/instance_lifecycle.py` `pause_instance_cascade` | (no bus call) | `dependency_bus.cancel_for_target(root_id)` (under flag) |
| `daemon/services/instance_lifecycle.py` `terminate_instance` | (no bus call) | `dependency_bus.cancel_for_target(instance_id)` (under flag) |
| `daemon/services/job_processor.py` `_process_next_job` | `job_type='message'` branch | branch removed; pause pre-check on `start_job` |

---

## Earlier (2026-06-20) — Phase A + B + C

- **Phase A** (premature-completion bug class): `USE_LEGACY_WAITING_FOR_CASCADE` and `DEBUG_COMPLETION_INVARIANT` flags; `CorrelationManager` is the authoritative completion mechanism; `waiting_for` is a rebuild-only cache per ADR-011. Fixed Race #1, Race #3, Race #5, the cross-dispatcher checkpoint corruption, and the sync/async deadlock.
- **Phase B** (`watch_job` Variant B): `watch_job` now routes through the CorrelationManager via `pending_jobs`; eliminates the `watch_job` fire-and-forget premature-completion bug class.
- **Phase C** (single dispatcher): `MessageJobHandler` demoted to cross-instance handoff only (C-M5); ExecutionGate collapsed from DB-backed lease to per-instance `asyncio.Lock` (C-M6, ~700 lines → ~40); all WorkerPool + JobQueue dispatch paths share a unified `MessageProcessingPipeline`; pause/terminate matrix regression-tested.
