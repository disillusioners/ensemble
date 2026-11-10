# Phase 5: Contract, Config, and Observability — rev 2

> **rev 2 — supersedes rev 1 (commit 36a9e9c0d) per council cycle 1.**
> Binding source of truth: `architecture-recommendation.md` §6 (consumer-gate
> evidence — the one-time gate artifact), §4.3 (envelope + negative space), DQ7
> (config surface), DQ8 (observability), §7 (module decomposition), §8.4 (Phase-5
> mandate). Where this file and that document disagree, **that document wins**.
> Rev-1 content that survives is carried forward with task numbers continuous
> (T1…T10 preserved; new work appended from T11).
>
> **Phase 5 has SHRUNK** (arch doc §8.4): the envelope helper and the consumer-gate
> artifact moved to **Phase 2**; Phase 5 is now the **config class + the
> observability surfaces + the fence re-run**.

## Objective

Own the three **externally-observable** surfaces of the feature — the configuration
knobs an operator turns, the observability surfaces an operator reads, and the
**fence re-run** that proves the feature never touched the T4 seam. This phase
writes **no enforcement logic**; the envelope producer lives in Phase 2
(`ram_guard/errors.py`).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Record the DQ6 CONSUMER GATE as a one-time artifact — do NOT re-derive it.** The gate is **SATISFIED by evidence**: `architecture-recommendation.md` §6 lists **22 verified consumers, zero break**. Rev 1's Task 1 named the **wrong file**: `tests/test_chart_tools_legacy_error_contract.py` pins **chart tools only** (58 lines; imports neither bash nor proc) — **inapplicable** (arch doc §1.1 P6). The real pin surface is the `proc_status` substring assertions (`tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1831`) and the `ProcessInfo.status == "killed"` attribute pins | Phase 2 Task 16 | The §6 artifact is **referenced**, not re-derived; the four strings that must never change (the bash success string, the bash timeout string, the `proc_status` block, the `proc_list` header) are pinned by the Phase 6 suite; **no** phase repeats a per-phase consumer ritual |
| 2 | **Define the master kill-switch** `ENSEMBLE_RAM_GUARD` — bare `ENSEMBLE_*`, reaper semantics (`bash_spawn_reaper.py:32-60`): **default ON when absent**; `0/false/no/off` (case-insensitive) = OFF everywhere; explicit anything else = ON. **OFF is a COMPLETE no-op**: no thread, no registration, no measurement, **zero DB queries**, one INFO line at lifespan start | Phase 1 Task 7 | The parsing is a pure function with the exact reaper semantics; OFF ⇒ `app.state.ram_guard is None` and **no thread exists** |
| 3 | **Implement the config class** `RamGuardConfig(BaseSettings)` with `env_prefix="RAM_GUARD_"`. Knobs — **INT-only** (house rule: no human-string parsing), with the **ruled** bounds: `RAM_GUARD_CEILING_MIB` default **3072**, bounds **[64, 65536]**; `RAM_GUARD_POLL_INTERVAL_MS` default **200**, bounds **[50, 2000]**; `RAM_GUARD_GRACE_MS` default **250**, bounds **[0, 2000]**; `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` default **250**, bounds **[50, 2000]**. **Depth/node caps are module constants, not knobs.** (Rev 1's `le=1048576` is superseded — the ruled clamp is **[64, 65536]**.) The class loads with no env set and yields exactly these defaults | Phase 1 Task 7 | Every knob has a name, unit, bound, env var, and default; the class loads with no env set and yields the documented defaults; the ceiling converts once to bytes (`3072 × 1 048 576 = 3 221 225 472`) |
| 4 | **Keep the YAML block COMMENT-ONLY.** A literal in `config.yaml` silently beats the env tier (pydantic init-kwargs outrank env — `config.py:911-916`), so the new class's yaml block documents the defaults in comments only | Task 3 | A test asserts a yaml literal does **not** take precedence over the env tier for this class (the trap is asserted, not assumed) |
| 5 | **Accept the boot-abort semantics.** Bounds are validated at `load_config()` **outside** the try/except ⇒ an out-of-range env value **aborts boot** (house-approved; `config.py:911-916`, `api.py:2266-2278`). Do **not** silently clamp | Task 3 | A config-ladder test asserts the abort. **Testability note:** the abort rung is asserted **only if** a daemon-boot harness exists; if it does not, the rung is recorded as an **explicit untestable disclosure** (Phase 6 T7) rather than asserted-away |
| 6 | **Keep the ceiling runtime-injectable** — the config value is the **default**; an explicit per-call override threads down to the guard's `register()`. **SECURITY RULING:** the override is an **internal/test seam only** — the LLM-facing bash/proc tool schemas gain **NO** ceiling parameter (an agent-visible knob would let the agent raise its own ceiling) | Phase 1 Task 7 | A per-call ceiling overrides the config default; the default path uses the config value; both lanes honour the override; the tool schemas are unchanged (asserted) |
| 7 | **Implement the durable event.** `EventKind.RAM_LIMIT_EXCEEDED = "ram_limit_exceeded"` — **the same string as the envelope `code`** (both are machine-facing verdict records of the same act; `exit_kind` stays a separate process-outcome taxonomy). Payload = **the envelope key set + the verdict extras** (`metric`, `trigger_metric`, `confirmed_pss_bytes`, `under_report_bound_bytes`, `members_total/killable/unreachable`). **Exactly one event per breach**, driven by the `WATCHING→KILLED` transition — **never per tick**. A spawn with `instance_id=None` ⇒ **counter + log only** (no event) | Phase 2 Task 16 | Exactly one event per breach; the event kind string equals the envelope `code`; the payload key set is asserted |
| 8 | **Enforce the PRODUCER-SIDE ALLOW-LIST.** The event bus persists `data` **unsanitized** (`event_bus.py:176-183`) ⇒ the producer must allow-list. Forbidden in the payload: argv (incl. hashes), env keys/values, stdout/stderr, secret ids, per-member pid lists / RSS breakdowns, host paths/hostnames/workdir, exception reprs | Tasks 7, 9 | A test asserts the absence of every forbidden class; the payload carries **usage numbers only** |
| 9 | **Implement the NEGATIVE SPACE in the message** (§4.3): `message` is **fixed text with numeric interpolation only** — the ceiling and observed peak with human units; `observed_peak_bytes` labelled a **sampled lower bound** of the **named metric** (+ the shared-page policy); the statement that the **daemon's RAM guard** (not the kernel OOM killer) issued the kill; tree completeness; a recovery hint (a kill can land mid-write — re-run idempotently). **NO argv, no sanitized argv, no argv hash** — the sanitized form still leaks the `--flag value` residual | Task 8 | A message containing the offending string is rejected; the template has **no** argv interpolation path; the message names the metric and the lower-bound label |
| 10 | **Implement the counters** (RAM-only, `SamplerCounters` shape): `ram_kills`, `ram_breaches_detected`, `ram_kill_refusals` **per-predicate** (P0…P8), `ownership_unverified`, `tree_incomplete` | Phase 2 Task 14 | Each counter increments on exactly its condition; the refusal counter is keyed by predicate |
| 11 | **Implement the log line** with the prefix **`ram-guard:`** (matching the house `resource-monitor:` convention). Rev 1 did not name the prefix. **The tick path never logs** (Phase 2 T13); only state transitions log | Task 10 | The prefix is asserted; a tick emits no line; a breach/kill/refusal transition does |
| 12 | **Implement the NEW read surface — `GET /api/resources/ram-guard`.** Rev 1's Task 9 wrongly called this "existing": it is **NEW** and **additive**. It carries the counters **plus** the guard health heartbeat: `enabled`, `thread_alive`, `last_tick_at`, `missed_samples`, `ticks`, `registry_depth`. It is **NOT** in `/livez` or `/readyz` — an observability death must **not** gate traffic. `/control-capabilities` stays **byte-identical** (fence-pinned) | Tasks 10, 11 | The endpoint exists, returns the counters + the heartbeat, and is not referenced by `livez`/`readyz`; `/control-capabilities` is unchanged |
| 13 | **Re-run the T4 fences** — `tests/test_resource_control_seam.py` **unmodified** (both grep fences stay green: no `daemon/` file names `action_by`/`acted_at`; no `daemon/` file combines `resource_pid_map` with the word `action`), and `/control-capabilities` is byte-identical | Phase 1 Task 9 | The fence pack passes unmodified; the new endpoint file names **neither** the dormant columns **nor** the `resource_pid_map` string |
| 14 | **Verify the observability payload contains no secrets** — a command whose env overlay carries a secret marker must produce no marker value in any log line, event payload, or persisted record | Tasks 7, 8, 9 | Zero occurrences of the marker value in logs, events, and records (an asserted test, not a code review) |
| 15 | **Verify no argv in ANY surface** — the envelope, the event payload, the log line, and the endpoint response are all scanned for the command's argv shape | Tasks 8, 9, 12 | A test asserts the argv string appears in **none** of the four surfaces |

## Coupling

- **Tight with:** Phase 2 (the counters, the heartbeat, and the envelope producer live
  there), Phase 3 and Phase 4 (both read the knobs and bump the counters),
  Phase 1 (every shape is the ruling).
- **Independent of:** the `resource_monitor` package — the guard's counters are
  **RAM-only** in the `SamplerCounters` **shape**, not in that package.
- **Fence-coupled to:** the resource-control seam — this phase must **not** name the
  dormant columns or the `resource_pid_map` string in any `daemon/` file (the grep
  fences in `tests/test_resource_control_seam.py:296-346`).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| A tool-output consumer parses the tool's return string and breaks on the new envelope | **High** | Low | The gate is **already satisfied by evidence** (22 consumers, zero break — arch doc §6); Task 1 references it instead of re-deriving; the four never-changing strings are pinned by the Phase 6 suite |
| An argv shape leaks into the envelope, the event, the log, or the endpoint | **High** | Medium | Tasks 8, 9, 15 enforce the negative space at the producer **and** scan all four surfaces; the sanitized form is explicitly forbidden (it still leaks the `--flag value` residual) |
| A yaml literal is added to `config.yaml`, silently killing the env tier | Medium | Medium | Task 4's test asserts the env tier still wins; the yaml block is comment-only by construction |
| An out-of-range env value aborts the daemon in production (a foot-gun) | High | Low | Generous bounds; the abort is house-approved and loudly logged; the kill-switch OFF path is unaffected by a bad ceiling (the switch is checked first) |
| The event fires per tick ⇒ an event storm | Medium | Medium | Task 7 requires exactly one event per breach, driven by the state transition |
| The guard's health heartbeat is wired into `livez`/`readyz`, so a dead guard gates traffic | **High** | Low | Task 12 forbids it explicitly; the endpoint is additive and independent |
| The fence test trips because the new endpoint file names `resource_pid_map` near the word `action` | Medium | Medium | Task 13's fence re-run is a phase exit gate; the endpoint file names neither |
| The counter names drift from the arch doc's DQ8 list | Low | Medium | Task 10 asserts the counter names as an exact set |

## Exit Criterion

- The config class loads with no env set, honours every `RAM_GUARD_*` knob at its
  **ruled** default and bound, keeps the yaml block comment-only, and aborts boot on
  an out-of-range value (asserted, or an explicit untestable disclosure).
- The master switch `ENSEMBLE_RAM_GUARD` implements the reaper semantics and OFF is
  a **complete no-op** (no thread, no registration, no measurement).
- The ceiling is **runtime-injectable per call** as an internal seam; the
  LLM-facing tool schemas are **unchanged** (asserted).
- Observability: exactly one event + the counter increments + a `ram-guard:`-prefixed
  log line per breach, with **zero** secret material and **zero** argv anywhere.
- `GET /api/resources/ram-guard` exists, is additive, carries the counters + the
  heartbeat, and is **not** referenced by `livez`/`readyz`.
- `tests/test_resource_control_seam.py` passes **unmodified**;
  `/control-capabilities` is byte-identical.
