# Phase 5: Contract, Config, and Observability

## Objective

Fix the three **externally-observable contracts** of the feature — the typed error
returned to the agent, the configuration knobs an operator turns, and the
observability surfaces an operator reads — and verify that none of them breaks an
existing contract.

This phase touches **no enforcement logic**; it owns the shapes that Phases 3 and 4
consume. It runs **in parallel** with Phases 3/4 only after the Phase 1 rulings
(DQ6/DQ7/DQ8) fix the shapes.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Verify the legacy error contract BEFORE fixing the shape.** Read `tests/test_chart_tools_legacy_error_contract.py` and **grep the tool-output post-processors** for consumers of the `ok` / `code` / `surface` keys. This is a hard gate: the envelope shape may not be committed until the consumer list is known | Phase 1 Task 6 | A written list of every consumer of the envelope keys, with each consumer's tolerance of a new `surface` value and new sibling fields |
| 2 | **Implement the typed envelope** as a single shared helper in `daemon/services/ram_guard/errors.py`, mirroring `_format_secret_envelope` (`bash.py:662-678`): JSON `{"ok": false, "code": …, "surface": …, "message": …}` returned **as a string** (tools never raise), with the ruled additional fields. The proc lane's `surface` differs from the bash lane's | Phase 1 Task 6 | One helper, two `surface` values, one `code`; both lanes emit it; the shape parses as JSON in a test |
| 3 | **Define the message text** — it must name the ceiling, the observed peak, the metric the number represents, and the fact that the tree was killed. It must **not** leak the command's secrets (only the sanitized executable + argv-shape, per `_sanitize_command_for_registry`, `bash.py:966-979`) | Task 2 | The message states "which metric" — a reader is never misled about what the number means (DQ1's double-count caveat must be visible when applicable) |
| 4 | **Implement the config class** per the DQ7 ruling: a dedicated class with its own `env_prefix` (house rule — per-class prefix families; a bare `ENSEMBLE_*` does **not** bind to a prefixed class), **INT-only** sizes (MiB, with `ge`/`le` bounds — house rule: no human-string parsing), the master kill-switch defaulting **ON** with an explicit env-off, the poll interval default + bounds, and the grace default + bounds | Phase 1 Task 7 | Every knob has a name, unit, bound, env var, and default; the class loads with no env set and yields the documented defaults |
| 5 | **Keep the YAML block COMMENT-ONLY.** A literal in `config.yaml` silently beats the env tier (pydantic init-kwargs outrank env — `config.py:911-916`), so the new class's yaml block documents the defaults in comments only | Task 4 | A test asserts a yaml literal does **not** take precedence over the env tier for this class (the trap is asserted, not assumed) |
| 6 | **Accept the boot-abort semantics.** Bounds are validated at `load_config()` **outside** the try/except ⇒ an out-of-range env value **aborts boot** (house-approved; `config.py:911-916`, `api.py:2266-2278`). Do **not** silently clamp | Task 4 | A config-ladder test asserts the abort (and the process exits with the house's abort shape, not a traceback) |
| 7 | **Make the ceiling runtime-injectable** — the config value is the **default**, and an explicit per-call override threads down to the guard. This is the mechanism the bounded test harness depends on (a boot-env-only knob would force a daemon restart with a small ceiling) | Phase 1 Task 7 | A per-call ceiling overrides the config default; the default path uses the config value; both lanes honour the override |
| 8 | **Implement the observability surfaces**: the logger line (module-prefixed, e.g. `ram-auto-termination: …`), the in-process counter (the `SamplerCounters` shape, `sampler.py:243-284`), and the durable event (`EventBus.create_event(instance_id, kind, data)`, `event_bus.py:155-191`) with an **additive** `EventKind`. The event fires **once per breach** — never per poll tick — with the payload `{code, surface, observed_peak_bytes, limit_bytes, pid, pgid}` | Phase 1 Task 8 | One breach ⇒ exactly one event, one counter increment, and log line(s) with no secret material |
| 9 | **Read the counters** through the existing resources endpoint family (`daemon/routers/resources.py`) **without changing** the `/control-capabilities` response shape (it is pinned by `tests/test_resource_control_seam.py`) | Task 8 | The fence test passes **unmodified**; the counter is readable; `/control-capabilities` is byte-identical |
| 10 | **Verify the observability payload contains no secrets** — a command whose env overlay carries a secret marker must produce no marker value in any log line, event payload, or persisted record | Tasks 3, 8 | Zero occurrences of the marker value in logs, events, and records (an asserted test, not a code review) |

## Coupling

- **Tight with:** Phase 3 and Phase 4 (they emit the envelope, read the knobs, and
  bump the counters), Phase 1 (every shape is the ruling).
- **Independent of:** Phase 2 (the guard does not own the envelope) and Phase 7
  (the runbook documents the shapes; it does not define them).
- **Fence-coupled to:** the resource-control seam — this phase must **not** name the
  dormant columns (`action` / `action_by` / `acted_at`) or the `resource_pid_map`
  string in any `daemon/` file (the grep fences in
  `tests/test_resource_control_seam.py:296-346`).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| A tool-output post-processor parses the tool's return string and breaks on the new envelope | High | Medium | Task 1 is a **gate** — the consumer list is produced before the shape is written; Task 2 is not started until it exists |
| The new `surface` value (`bash.exec` / `proc.exec`) collides with an existing surface registry | Medium | Low | The consumer grep in Task 1 surfaces the surface registry; a test pins the surface strings |
| A yaml literal is added to `config.yaml` by a well-meaning hand, silently killing the env tier | Medium | Medium | Task 5's test asserts the env tier still wins; the yaml block is comment-only by construction |
| An out-of-range env value aborts the daemon in production (a foot-gun) | High | Low | Generous bounds (a ceiling of MiB with a wide `le`); the abort is house-approved and loudly logged; the kill-switch OFF path is unaffected by a bad ceiling (the switch is checked first) |
| The event fires per tick ⇒ an event storm | Medium | Medium | Task 8's acceptance requires exactly one event per breach; the counter is a breach counter, not a tick counter |
| The fence test trips because the guard names `resource_pid_map` near the word `action` | Medium | Medium | This phase uses a RAM-only substrate and never names the dormant columns; the fence test is run unmodified in the exit criterion |

## Exit Criterion

- The typed envelope is implemented once, emitted by both lanes, with the ruled
  fields and a message that states the metric's meaning.
- **The consumer check (Task 1) is complete and written down** — this is the gate
  that makes the shape safe.
- The config class loads with no env set, honours every env knob, keeps the yaml
  block comment-only, and aborts boot on an out-of-range value (asserted).
- The ceiling is **runtime-injectable per call** — proven by a test that injects a
  small ceiling without touching the environment.
- Observability: exactly one event + one counter increment + log line(s) per breach,
  with **zero** secret material in any of them.
- `tests/test_resource_control_seam.py` passes **unmodified**.
