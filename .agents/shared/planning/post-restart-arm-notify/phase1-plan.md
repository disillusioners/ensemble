# Phase 1: Arm-Side Record — `pending_wakes` journal extension + capture helpers

> **⛔ HARD CONSTRAINT (inherited from Phase 2, governs every task below):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**ADR basis:** ADR-039 (Wake record schema + write atomicity — `pending_wakes` keyed by `run_id` on the existing journal), ADR-044 (Kill-switch — `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` default ON, gates the arm-side write). Cross-cutting invariants 1, 4, 5, 8, 11 of `architecture-recommendation.md` §8 are asserted by this phase.

**Scope of environment:** all implementation work targets **demo** (`~/agents-ensemble-demo`, :7979, `ensemble_demo`) and **sandboxes** (own port + throwaway PG). Live target paths exist in the scripts behind guards but their execution is **USER-GATED** and never performed by this initiative.

**Convention note:** any new Python module this phase adds uses `from __future__ import annotations` for Python 3.13 import safety.

---

## Objective

Add the **durable wake record** to the existing upgrade journal
(`releases/state.json`) and integrate the arm-time capture into both
arm paths (`system_restart` + `system_upgrade`) such that the wake
record is **structurally inseparable** from the arm: a crash between
the two writes is impossible because both ride ONE `journal_write`
envelope under the SAME caller-acquired journal lock. A refused arm
(live env, pipeline-busy, journal-unavailable, etc.) writes no
record at all; a successful arm writes the wake record in the same
`journal_write` call as the `pending_op`, with the
`arm_pending_wake` call INSIDE the lock-holding `try` block
(atomicity revision per architecture delta #3 — see D5 and T12).

**Exit in one sentence:** on demo, a `system_restart` arm against
`TARGET=demo` produces a `releases/state.json` whose
`pending_wakes[run_id]` mirrors the `pending_op` (same `run_id`, same
`armed_at`, same `env`, full routing context) — and a refused live
arm produces no such record — both proven by journal reads and a
new unit pack (T1.1–T1.6, T4.4–T4.6, T5.12) at 100% green.

---

## Verified Starting Point (do not re-derive)

- The arm contract: `system_restart` (`upgrade_tools.py:2030-2268`,
  arm at `:2156-2263`) and `system_upgrade`
  (`upgrade_tools.py:2313-2900`, 3-factor gate at `:2494-2660`, arm at
  `:2707-2872`) both write a `PendingOp` (`upgrade_journal.py:708-742`)
  into `releases/state.json` BEFORE returning to the caller. The
  arm-side `armed_by_instance` field already carries the arming
  instance id — the wake record reuses this.
- The durable surface: `<install_dir>/releases/state.json` is the
  single-writer atomic surface (tmp+fsync+os.replace at
  `upgrade_journal.py:281-294`). `ensure_extensions`
  (`upgrade_journal.py:332-352`) is the additive extension point —
  every existing `pending_op` / `pending_restart` / `pending_actions`
  / `history` extension rides this helper. The wake record rides the
  same helper. **No** second file (rejected by D-FA1.1).
- The `pending_op` lifecycle: `restart.sh:250-262` clears
  `pending_op` + `pending_restart` + `in_flight` on completion and
  journals a `restart` terminal event. **Critical consequence:** a
  wake detector that looks only at `pending_op` would MISS completed
  restarts whose `pending_op` is already cleared. The wake record
  must be cleared on **delivery**, not on completion — it outlives the
  `pending_op` clear.
- The `from_json` filter discipline (`upgrade_journal.py:738`) — known
  fields preserved, unknown fields dropped silently — is the
  schema-evolution contract. The wake's `PendingWake.from_json`
  follows the same pattern (D-FA1.1).
- The source / routing context:
  `manager._user_origin_windows[instance_id]` (`manager.py:4239-4245`)
  and `manager._user_origin_last_stamp[instance_id]`
  (`manager.py:4230-4238`) are the in-memory per-instance stamps.
  They are wiped at boot (RAM-only). The wake record must PERSIST
  the source at arm time so the boot-time wake can re-stamp the
  window for the wake turn.
- The `journal_lock_acquire` / `journal_lock_release` pair is the
  per-env pipeline lock (FA2.1 atomicity argument; same envelope
  the `pending_op` write uses). The wake is written in the same
  envelope.
- Live-outright-refusal: `upgrade_tools.py:2051-2058` returns BEFORE
  any journal write for `system_restart` on live. The wake surface
  therefore structurally cannot originate from a live restart path
  (ADR-044 inheritance via D-FA5.5).

---

## Design Decisions (this phase)

**D1 — Wake record rides `ensure_extensions` + the same
`journal_write` envelope as the arm (D-FA1.1 + D-FA2.1, ADR-039).**
The wake is a new top-level key `pending_wakes: dict[str, PendingWake]`
on `releases/state.json`, keyed by `run_id` (the cross-death join
key already used by `pending_op` / `pending_restart` / `pending_actions`).
A new `PendingWake` dataclass in `daemon/tools/upgrade_journal.py`
mirrors the `PendingOp` shape: a `kwargs = {k: v for k, v in
data.items() if k in cls.__dataclass_fields__}` `from_json` filter
discipline. A new wrapper `arm_pending_wake(install_dir, wake)` calls
`ensure_extensions` (which loads the current journal) and writes the
wake record into `data["pending_wakes"]`, then calls the SAME
`journal_write` helper that `write_pending_op` already uses
(tmp+fsync+os.replace). **No second file** (D-FA1.3 ruling applied
upfront — see invariant 1).

**D2 — Lifecycle state machine is a 4-state status field
(D-FA1.2, ADR-039 + ADR-043):** `pending → delivering → delivered |
abandoned`. There is **no** `failed` state — a delivery failure
logs and continues (boot-never-wedge, Phase 2 ADR-044); the wake
record remains `pending` and is retried on the next tick. Transitions:
`pending → delivering` is CAS-style (the journal lock is the existing
serialization primitive); `delivering → delivered` and
`pending → abandoned` are unconditional. The `status` field is the
**idempotency key** (AC5) — an unrelated later restart observes a
CLEAN dict (the record was structurally removed on `delivered` or
`abandoned`) and re-delivery is impossible. The terminal-class
helpers `arm_pending_wake`, `mark_wake_delivering`, `mark_wake_delivered`,
`mark_wake_abandoned`, `list_pending_wakes` are all in
`daemon/tools/upgrade_journal.py` and all reuse `ensure_extensions`
+ `journal_write`.

**D3 — Arm-time capture uses existing in-memory state (D-FA2.2).**
Three helpers, all in the same module:

- `_capture_user_source(manager, instance_id) -> str` — reads
  `manager._user_origin_windows[instance_id]["source"]` (or `""` if
  absent). The source is wiped at boot (RAM-only); the capture is a
  PERSISTENT copy. `""` is the sentinel for "no user-origin at
  arm time" (non-chat arm path; Phase 2 ADR-040 wake turn will use
  `"api"` as the empty-source fall-back).
- `_capture_user_message_id(manager, instance_id) -> str | None` —
  reads `manager._user_origin_last_stamp[instance_id]["message_id"]`
  (or `None` if absent).
- `_resolve_agent_id(manager, instance_id) -> str | None` — reads the
  instance repo (`manager.instance_repo`); returns the agent id or
  `None` on a miss. A miss is **tolerated** — the wake's target is
  the instance id (always present), the agent id is informational
  for the agent's self-introspection. Defensive `try/except Exception`
  returns `None` on any repo error; the arm never blocks on the agent
  id resolution.

The capture is a **best-effort snapshot** at arm time. The wake
record carries the captured values verbatim. Re-stamping and
re-routing are Phase 2 concerns.

**D4 — Idempotency on retry (D-FA2.3, ADR-039).** The arm path can
be retried by the agent (e.g. a network blip in the agent's tool
call). The arm is itself idempotent on `run_id` (each retry mints a
new `run_id`); the wake is written per-`run_id`. A retry produces a
second wake record with a different `run_id`. The two records are
distinct; the Phase 2 sweep coalesces by `arming_instance_id` at
delivery time, not at arm time (the `run_id` is the wake's identity).

**D5 — The wake is structurally absent on refused arms.** The
arm-side envelope in `upgrade_tools.py` has a single `try` block —
the lock-holding `try` (lock via `journal_lock_acquire` at
`upgrade_tools.py:2167` restart / `:2719` upgrade; `journal_write`
itself has NO internal lock, so ALL serialization is this
caller-acquired lock, architecture delta #3) — that wraps
`write_pending_op` + `arm_pending_wake` + the in-memory
marker set + the lock release. `arm_pending_wake` MUST sit inside
this lock-holding `try` (T12 pins the placement; Phase 3 T6.4
structurally pins the call-site position). A `JournalTorn` / `OSError` /
`KeyError` in the envelope triggers the existing
`except (JournalTorn, OSError, KeyError) as exc:` block (system_restart
`:2209-2236`, system_upgrade `:2850-2852`) which unwinds
`in_flight` and releases the lock. **The wake record is also written
at the same journal write** — so a `JournalTorn` that triggers the
unwind means the arm FAILED and the wake was NEVER written. There
is no asymmetry to handle. The `pending_op` is rolled back to `None`
by the unwind; the wake record, having never been written, is absent.
The system is consistent (invariant 5).

**D6 — Live-outright-refusal inheritance (D-FA5.5, ADR-044).** The
arm-side wake write is **inside the try block that follows the
live-outright-refusal return** (system_restart `:2051-2058`). A live
arm returns BEFORE any journal write; the wake write is unreachable
on live. The kill-switch (`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`)
additionally short-circuits `arm_pending_wake` to a no-op so an
operator opt-out produces no journal writes either.

**D7 — The wake is cleared on delivery, not on completion (D-FA1.2
+ D-FA2.1, ADR-039).** `restart.sh:250-262` clears `pending_op` +
`pending_restart` + `in_flight` on completion. **The wake is NOT
cleared by executor completion** — the wake survives the executor's
clearing. The boot sweep (Phase 2) sees the wake, observes the
terminal event, and delivers. The wake is cleared by the sweep
(`mark_wake_delivered`) AFTER successful `enqueue_message`. This is
the load-bearing reason the wake record MUST live on a separate
journal key (FA1.1).

---

## Components (file-level touch list)

### Source files modified

| File | What changes | Mandate (architecture section + ADR) |
|---|---|---|
| `daemon/tools/upgrade_journal.py` | Add `PendingWake` dataclass (mirrors `PendingOp`); add `arm_pending_wake`, `mark_wake_delivering`, `mark_wake_delivered`, `mark_wake_abandoned`, `list_pending_wakes` lifecycle helpers + a parameterized history walker `latest_matching_event(journal, run_id, armed_at, events)` (the WAKE event-set constant is NOT defined in Phase 1 — Phase 2 adds `WAKE_TERMINAL_EVENTS` and the wake-owned wrapper per architecture delta #1); extend `ensure_extensions` to include the `pending_wakes` key (default `{}`); add the `_WAKE_STATUS_*` lifecycle constants; preserve the `from_json` filter discipline (line 738) | D-FA1.1, D-FA1.2, D-FA2.1, D-FA3.1 → ADR-039, ADR-040 (Phase 2 hook surface); D-FA4.1 (predicate centralized, ADR-042 — event-set ownership per architecture delta #1); D-FA5.4 (best-effort, ADR-044) |
| `daemon/tools/upgrade_tools.py` | (a) Add `_capture_user_source`, `_capture_user_message_id`, `_resolve_agent_id` helpers at module scope (or a small `_arm_capture` module); (b) in `system_restart` arm block at `:2156-2263`, add `arm_pending_wake` call in the same `try` block as `write_pending_op` (with kill-switch guard); (c) in `system_upgrade` arm block at `:2707-2872`, add the same. The wake write rides the existing `except (JournalTorn, OSError, KeyError) as exc:` catch (`:2209-2236` and `:2850-2852`). | D-FA2.1, D-FA2.2, D-FA2.3, D-FA5.5 → ADR-039, ADR-044 |
| `daemon/__init__.py` (or wherever the env-var pattern lives) | No new constant; the kill-switch is read inline at the two arm sites (system_restart `:2051-2058` already reads `self_env` — the kill-switch is one more `os.environ.get("ENSEMBLE_POST_RESTART_ARM_NOTIFY", "1")` line). Documented in code comment. | D-FA5.5 / D-FA6.2 → ADR-044 |

### Test files created

| File | What it covers | Test-strategy case IDs |
|---|---|---|
| `tests/unit/tools/test_post_restart_arm_notify_journal.py` | `PendingWake` round-trip + garbage tolerance; atomicity with `pending_op` (single envelope under the caller-acquired lock); CAS on `mark_wake_delivering`; structural removal on `mark_wake_delivered`; `mark_wake_abandoned` journals a `wake_abandoned` history event; terminal-state walker (`latest_matching_event`) returns event name / None / None on torn; non-interference with `clear_pending_op`, simulated `restart.sh` journal, `reconcile_pending_op`; live-outright-refusal writes no wake | T1.1, T1.2, T1.3, T1.4, T1.5, T1.6, T4.1, T4.2, T4.3, T4.4, T4.5, T4.6, T5.12 |

### Files NOT touched (explicit non-modification)

- `daemon/api.py` — no new HTTP endpoint (invariant 3, ADR-040, Phase 2).
- `daemon/services/upgrade_journal_sweep.py` — Phase 2 concern; the
  sweep's wake branch is added in Phase 2 (the module is read here
  only to confirm the boot order documented in R-10 of
  `risk-register.md`).
- `daemon/migrations/` — no new SQL migration; the wake is a JSON
  key on the existing journal (D-FA1.1 explicitly rejected a new
  table; invariant 4). **ADR-039 says journal-section — no new
  table — therefore no new `.sql` file in `daemon/migrations/`.**
- `daemon/manager.py` — no new public method; the `enqueue_message`
  surface is reused in Phase 2. The Phase 1 capture helpers read
  the existing `manager._user_origin_windows[instance_id]` and
  `manager._user_origin_last_stamp[instance_id]` (read-only).
- `scripts/upgrade/restart.sh`, `promote.sh`, `lib.sh`,
  `stage.sh`, `ledger_check.py` — unchanged. The wake is a daemon
  concern; the upgrade scripts do not touch it.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **T1** | **Add `PendingWake` dataclass + lifecycle state constants to `daemon/tools/upgrade_journal.py`** — mirror the `PendingOp` shape: `from __future__ import annotations`; `from dataclasses import dataclass, field; from typing import Any`; fields per `architecture-recommendation.md` §FA1.1 schema (run_id, kind, env, arming_instance_id, arming_agent_id, source, message_id, message_metadata, target_version, mode, armed_at, expires_at, abandon_after, status, delivered_at, delivered_message_id); `from_json` classmethod with the line-738 `kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}` filter discipline; `to_json` mirror. Add `_WAKE_STATUS_PENDING/_DELIVERING/_DELIVERED/_ABANDONED` constants | none | `PendingWake.from_json({"run_id": "r-x", "kind": "restart", "env": "demo", "status": "pending", ...})` round-trips through `to_json` byte-exact; unknown fields dropped silently; missing required fields raise `ValueError` with the field name |
| **T2** | **Add `arm_pending_wake(install_dir, wake: PendingWake) -> None` wrapper** — calls `ensure_extensions` (which loads the current journal), writes `wake.run_id → wake` into `data["pending_wakes"]`, then calls the SAME `journal_write` helper that `write_pending_op` uses. The function is a single `journal_write` call; no separate file. The kill-switch check (`os.environ.get("ENSEMBLE_POST_RESTART_ARM_NOTIFY", "1") != "0"`) is the first line — when off, the function returns without writing | T1 | Unit test: a `PendingWake` written via `arm_pending_wake` appears in `data["pending_wakes"]` after `journal_read`; a second `arm_pending_wake` for the same `run_id` OVERWRITES (per-arm semantics — a re-arm of the same run is the same record); kill-switch on → no write, no exception |
| **T3** | **Add `mark_wake_delivering(install_dir, run_id) -> PendingWake \| None`** — CAS-style: acquires the journal's per-env pipeline lock; on success, sets `status = "delivering"`, calls `journal_write`; returns the new `PendingWake`. On lock-not-acquired, returns `None` and logs WARNING. The lock reuses the existing `journal_lock_acquire` (same primitive `pending_op` uses) | T1, T2 | Concurrent callers: the lock-holder's call returns the new record; the other caller's call returns `None`; the second caller's `WARNING` log line is grep-able |
| **T4** | **Add `mark_wake_delivered(install_dir, run_id, message_id: str) -> None`** — unconditional write: sets `status = "delivered"`, `delivered_at = now_iso()`, `delivered_message_id = message_id`, then REMOVES the record from `data["pending_wakes"]` (the structural removal is the idempotency key — invariant 7). The dict shrinks on every `journal_write` that follows | T1, T2 | After `mark_wake_delivered`, `list_pending_wakes` does NOT include the `run_id`; the record is gone from the journal entirely |
| **T5** | **Add `mark_wake_abandoned(install_dir, run_id, reason: str) -> None`** — terminal-cleanup transition: sets `status = "abandoned"`, removes from dict, then calls `journal_history_append(install_dir, "wake_abandoned", f"run_id={run_id} reason={reason}")` for forensics. Uses the same `journal_history_append` helper as the existing pipeline events | T1, T2, T4 | After `mark_wake_abandoned`, the record is gone from the dict; the journal `history` ends with an event whose `name == "wake_abandoned"` and `run_id` matches |
| **T6** | **Add `list_pending_wakes(install_dir) -> list[PendingWake]`** — defensive reader: returns `[]` on `JournalTorn`; on any `pending_wakes` value that is not a `dict` (e.g. `null`, `[]`, `"<str>"`, `123`), returns `[]` (the empty-list sentinel; mirrors `PendingOp` garbage tolerance per R-20). Each value validated via `PendingWake.from_json` (which itself drops unknown fields and raises on missing required) | T1, T2 | Garbage-tolerance test: `pending_wakes = null` / `[]` / `"<str>"` / `123` all return `[]` without raising; a `pending_wakes` with one well-formed record returns a one-element list; a `pending_wakes` with one malformed record (missing required field) is logged + skipped (the other records are returned) |
| **T7** | **Add `latest_matching_event(journal, run_id, armed_at, events) -> str \| None` — parameterized history walker** (REWRITTEN per r4 fold C1: journal history entries are FLAT `{"ts": <iso>, "event": <name>, "detail": <prose>}` records at `upgrade_journal.py:326` and `lib.sh:663,666` — there is NO `run_id` field on history entries; promote-lane terminal events (`promote.sh:366`; `rollback.sh:203,209,211`) carry NO `run_id` at all, only restart-lane detail PROSE embeds `run_id=<id>` as an OPTIONAL tie-breaker). The walker is **TS-SCOPED + EVENT-CLASS-MEMBERSHIP** (mirroring `_terminal_event_after` at `upgrade_journal.py:986`), reading the `event` field (NOT `name`) and matching against the passed `events` tuple. The `run_id` parameter is accepted for the RESTART-LANE OPTIONAL TIE-BREAKER ONLY (the wake's `detail_substring_contains` check on the RESTART lane — see `phase2-plan.md` T13 wake-owned reader). The WAKE event-set constant is NOT defined in Phase 1 — Phase 2 adds `WAKE_TERMINAL_EVENTS` and the wake-owned wrapper per architecture delta #1. Reads the journal dict, walks `history`, returns the event name of the latest entry matching BOTH the `event` class membership AND the `armed_at` scope (entry `ts >= armed_at`), or `None` if no match. `JournalTorn` → `None` (best-effort; the sweep's caller logs and continues). Phase 2 wraps this walker with `WAKE_TERMINAL_EVENTS` to form the wake-owned terminal reader (mirroring `_terminal_event_after` `:986`, but armed_at-scoped, restart-lane-aware) — the PROMOTE-only `_TERMINAL_EVENTS` set is never mutated (reconcile semantics at `:1016` depend on the 6-member set) | T1, T6 | Test T4.1: a history ending in `{"ts": <iso>, "event": "commit", "detail": "..."}` returns `"commit"` for `run_id=<any>` (the `run_id` is not used for the event-class match on the promote lane). Test T4.2: a history with no matching event returns `None`. Test T4.3: a `JournalTorn` (simulated) returns `None`. **All fixtures use the REAL journal shape** `{"ts", "event", "detail"}` — the test asserts the schema on import (a fixture using `"name"` or `run_id` keys is a fail-loud import error, per `test-strategy.md` §2.0 r4 fold C1 note) |
| **T8** | **Wire the arm-side capture helpers into `system_restart` arm block (`upgrade_tools.py:2156-2263`)** — three new helpers at module scope: `_capture_user_source(manager, instance_id)`, `_capture_user_message_id(manager, instance_id)`, `_resolve_agent_id(manager, instance_id)`. Inside the existing `try` block (between the `write_pending_op` call and the in-memory marker set — i.e. INSIDE the lock-holding `try`, lock acquired at `:2167`, call site ~`:2209`, per architecture delta #3), add `arm_pending_wake(...)` with all fields captured. The kill-switch check is the first line. The existing `except (JournalTorn, OSError, KeyError) as exc:` catch at `:2209-2236` is unchanged — it covers BOTH the `pending_op` and the wake (single envelope, invariant 5) | T1–T7 | Demo: `system_restart(target=demo)` arm produces a `releases/state.json` with `pending_wakes[run_id]` carrying `run_id`, `kind="restart"`, `env="demo"`, `arming_instance_id=<id>`, `source=<recorded>`, `message_id=<id>`, `armed_at=<iso>`, `expires_at=<iso>`, `abandon_after=<iso+600s>`, `status="pending"`, and all informational fields populated; `restart.sh:250-262` clearing on completion does NOT touch `pending_wakes` (test T4.5); kill-switch on produces no record (test T5.12 on the live path) |
| **T9** | **Wire the same arm-side capture into `system_upgrade` arm block (`upgrade_tools.py:2707-2872`)** — identical pattern as T8, INSIDE the lock-holding `try` (lock acquired at `:2719`, call site ~`:2850`, after `write_pending_op`, before the in-memory marker set — architecture delta #3); identical catch coverage at `:2850-2852`. The `target_version` field is set to `op.target` for promote kind; `mode` is set to `op.mode` for restart kind, `None` for promote. The `abandon_after` is `op.expires_at + 600s` (PENDING_WAKE_GRACE_S, default 600) | T1–T8 | Demo: `system_upgrade(target=demo, target_version=vX.Y.Z)` arm produces `pending_wakes[run_id]` with `kind="promote"`, `target_version="vX.Y.Z"`, `mode=null`; same atomicity guarantees as T8; non-interference with `clear_pending_op` and `reconcile_pending_op` (tests T4.4, T4.6) |
| **T10** | **Phase-1 unit test pack at `tests/unit/tools/test_post_restart_arm_notify_journal.py`** — sync + `pytest-asyncio` where needed; `tmp_path` fixtures; `monkeypatch` for the kill-switch env; `unittest.mock.MagicMock` for the manager seam (capture helpers); convention precedent is `tests/unit/tools/test_upgrade_journal.py`. All test IDs T1.1, T1.2, T1.3, T1.4, T1.5, T1.6, T4.1, T4.2, T4.3, T4.4, T4.5, T4.6, T5.12, T6.7 (r4 fold C1 — the real-journal-shape structural pin). Every test docstring carries the AC + ADR-039/044 reference (per `test-strategy.md` §4.5). **ALL fixtures use the REAL journal history shape** `{"ts": <iso>, "event": <name>, "detail": <prose>}` (per `upgrade_journal.py:326` and `lib.sh:663,666`) — the test file asserts the schema on import (a fixture using `"name"` keys or top-level `run_id` on history entries is a fail-loud import error, per `test-strategy.md` §2.0 r4 fold C1 note). Pack invocation (per `.agents/tester/rules/ensure.md` Core #1 PACK-MAPPED discipline + r4 fold W3): the test is invoked via `bash test/packs/post_restart_arm_notify_journal_unit_test.sh`, NOT a bare `pytest` invocation. The pack is registered in `.agents/tester/PACKS.md` in the implementation-lane commit (`phase3-plan.md` T13) | T1–T9 | `bash test/packs/post_restart_arm_notify_journal_unit_test.sh` exits 0 with all T1.* + T4.1–T4.6 + T5.12 + T6.7 GREEN; the test assertion messages include the AC + ADR reference (grep-able) |
| **T11** | **Non-regression check: existing 30/30 + 124/124 + 142/142 packs remain green** — `tests/unit/tools/test_upgrade_journal.py` (the Phase 2 53-line pack), `tests/unit/tools/test_upgrade_tools.py` (the P2.2 tool-surface pack), `tests/test_release_journal.sh` (the shell-journal pack). The new code path adds to `ensure_extensions` (the additive extension point) and to the arm's `try` block (the additive capture call) — neither change can break the existing reader or the existing arm's refusal matrix | T1–T10 | Full test pack exits 0; the existing `PendingOp` round-trip + `journal_lock_acquire` + arm-refusal-token matrix all pass byte-exact; live pids verified unchanged on demo (per `test-strategy.md` §4.4 — the sandbox-isolation pattern) |
| **T12** | **Pin `arm_pending_wake` INSIDE the lock-holding `try` at BOTH arm sites (architecture delta #3, Phase-1 placement task)** — verify by source inspection + unit check that the `arm_pending_wake(...)` call sits INSIDE the same lock-holding `try` as `write_pending_op` at `system_restart` (lock acquired `upgrade_tools.py:2167`, call ~`:2209`) and `system_upgrade` (lock `:2719`, call ~`:2850`), in both cases AFTER the `write_pending_op` call. Rationale: `journal_write` (`upgrade_journal.py:254-294`) acquires NO internal lock — atomicity of arm+wake is BY the caller-acquired lock; a call placed outside the `try` (or before `write_pending_op`) is a silent torn-write risk. The Phase 3 structural test T6.4 pins the call-site position permanently | T8, T9 | Source-inspection assertion GREEN for both arm sites (call inside the `try`, after `write_pending_op`); a scratch-refactor moving the call outside the `try` FAILS the check loudly (full structural pin lands as Phase 3 T6.4) |

---

## Coupling

- **Tight with Phase 2 (ADR-040, ADR-041, ADR-042, ADR-043, ADR-044)** —
  the helpers minted here (`arm_pending_wake`, `mark_wake_delivering`,
  `mark_wake_delivered`, `mark_wake_abandoned`, `list_pending_wakes`,
  `latest_matching_event`) are the **public surface Phase 2 consumes**.
  Phase 2's `sweep_wake_records` calls `list_pending_wakes`,
  `mark_wake_delivering`, `mark_wake_delivered`, `mark_wake_abandoned`,
  and the wake-owned terminal reader (Phase 2 wraps the Phase 1
  walker with `WAKE_TERMINAL_EVENTS`). If the signatures or semantics
  change in Phase 1, Phase 2 must follow. ⟪SEAM (architecture delta
  #1): the WAKE predicate is grounded on the sibling constant
  `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` — NEVER on
  a mutated `_TERMINAL_EVENTS` (PROMOTE-only reconcile at
  `upgrade_journal.py:1016` depends on the 6-member set); Phase 3's
  T4.8 mutation guard pins both directions.⟫
- **Tight with the existing arm path (`upgrade_tools.py:2156-2263`
  and `:2707-2872`)** — the wake write is in the same `try` block
  as the `pending_op` write, and the catch is the same
  `except (JournalTorn, OSError, KeyError) as exc:` block. A
  change to the catch's semantics must consider the wake too
  (single envelope, invariant 5).
- **Loose with `restart.sh:250-262`** — the executor's terminal
  clearing does NOT touch `pending_wakes` (D-FA2.1 explicit
  requirement). The executor's journal writes are unchanged; the
  test T4.5 asserts the non-interference.
- **Loose with `reconcile_pending_op` (`upgrade_journal.py:1002-1067`)** —
  the PROMOTE-kind-only consumer does not touch `pending_wakes`
  (test T4.6). The wake sweep (Phase 2) is the only consumer
  that knows the wake record exists.
- **Independent of** the existing `MessageQueue` /
  `enqueue_message` surface — Phase 1 produces the durable record
  only; delivery is Phase 2.

---

## Per-Phase Verification (test-strategy.md mapping)

| Test ID | Description | Where | Verifies |
|---|---|---|---|
| **T1.1** | `PendingWake` round-trip — every field preserved; unknown fields dropped | `tests/unit/tools/test_post_restart_arm_notify_journal.py` | AC1 (arm-time durable record) + ADR-039 schema |
| **T1.2** | `PendingWake.from_json` garbage tolerance — `null` / `[]` / `"<str>"` / `123` → `[]` | same | R-20 mitigation; invariant 1 (no new file) |
| **T1.3** | `arm_pending_wake` atomic with `write_pending_op` — single `journal_write` envelope (verified by `os.replace` spy) | same | D-FA2.1 atomicity; invariant 5; R-4 mitigation |
| **T1.4** | `mark_wake_delivering` CAS — lock-holder succeeds, lock-loser returns `None` | same | D-FA1.2 state machine; R-11 (concurrent arms) |
| **T1.5** | `mark_wake_delivered` removes from dict — idempotency key | same | AC5 (one-shot delivery); invariant 7; R-9 mitigation |
| **T1.6** | `mark_wake_abandoned` journals `wake_abandoned` history event with reason | same | ADR-042 abandonment; R-5 forensics |
| **T4.1** | `latest_matching_event` walker returns event name for matching history | same | ADR-042 terminal-state predicate (walker; event-set owned by Phase 2) |
| **T4.2** | `latest_matching_event` walker returns `None` when pending | same | ADR-042 hold-pending semantics |
| **T4.3** | `latest_matching_event` walker returns `None` on `JournalTorn` | same | ADR-044 boot-never-wedge; R-12 |
| **T4.4** | `clear_pending_op` does NOT touch `pending_wakes` | same | D-FA2.1 explicit non-interference; load-bearing reason for separate key |
| **T4.5** | Simulated `restart.sh` terminal event + `clear_pending_op` preserves `pending_wakes` | same | Same — the executor's terminal-clearing must not clear the wake |
| **T4.6** | `reconcile_pending_op` (PROMOTE-kind only) does not touch `pending_wakes` | same | D-FA3.1 wake-sweep is the only wake consumer |
| **T5.12** | `system_restart` with `self_env=live` writes no wake record | same | D-FA5.5 / ADR-044 live-outright-refusal inheritance; invariant 8 |

**Pre-Phase-1 baseline:** the existing 30/30 `test_upgrade_journal.py`
+ 142/142 `test_upgrade_tools.py` packs remain green (T11).
**Post-Phase-1 invariant:** every new test docstring carries the
AC + ADR-039/044 reference (test-strategy.md §4.5).

---

## Risks (phase-specific — full register: sibling `risk-register.md`)

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R1.1 | The capture helpers read in-memory state that may be `None` for a non-user-origin arm — the wake record carries `""` or `None` instead of a real source | Low | Sentinel discipline: `""` for source, `None` for message_id / agent_id; Phase 2 wake turn uses `"api"` as the empty-source fall-back (ADR-041) |
| R1.2 | `arm_pending_wake` is called on every arm — a successful arm on every env (dev/demo/sandbox) writes a record; a 30-arm day creates 30 records, all `pending` until boot sweep runs | Low | Records are bounded by `expires_at + 600s` and removed on delivery / abandonment; a healthy daemon processes them within 90s; the `pending_wakes` dict is small (the file is a few KB) |
| R1.3 | The `from_json` filter discipline silently drops unknown fields — a future field added to `PendingWake` is invisible to an old binary reading a new journal | Low | The discipline is documented in `upgrade_journal.py:738`; Phase 1 follows the same pattern; the old binary never had a wake path, so the field drop is moot (R-17) |
| R1.4 | The `except (JournalTorn, OSError, KeyError) as exc:` catch in `upgrade_tools.py` was sized for a single `pending_op` write — adding a second write in the same try block doubles the failure surface | Low | Both writes are in the SAME `journal_write` envelope (D-FA2.1); a torn journal is the same torn journal for both; the catch's unwind logic (unwinds `in_flight`, releases lock) covers both equivalently |
| R1.5 | Live-outright-refusal at `upgrade_tools.py:2051-2058` could be moved by a future refactor — the wake write is then reachable on a live arm | **High** | The wake write is INSIDE the try block, AFTER the `if self_env == "live": return ...` check. A regression moves the live check, not the wake — but the test T5.12 catches it on every CI run. The test is a release-blocker regression pin |

---

## Rollback / Abandonment Notes

**If Phase 1 ships and Phase 2 does not:** the wake records are
written but never consumed. They accumulate in the journal's
`pending_wakes` dict up to `abandon_after`; the next successful
boot sweep (Phase 2) drains them. **No live impact** (live
outright-refusal pre-empts the write; the journal is the durable
surface; the wake record is a JSON key, not a row in a table — no
DB schema to migrate out).

**Abandonment (kill-switch):** `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`
in `.env` disables BOTH the arm-side write (T8/T9 first-line check)
AND the Phase 2 sweep's wake branch (Phase 2 design). Operators
opt out with one env-var flip; no code change required.

**Code rollback (if a Phase 1 commit lands and is found
broken):** the change is local to `daemon/tools/upgrade_journal.py`
(six new functions) + `daemon/tools/upgrade_tools.py` (three new
helpers + two `arm_pending_wake` calls in the existing `try`
blocks). A `git revert` of the commit is the rollback; the
arm-side code path returns to the pre-Phase-1 behavior (no wake
record, no impact on the existing arm contract). **No DB migration
to reverse** (ADR-039 is journal-section, not table; the dict
key is additive and old readers ignore it).

**What does NOT work as a rollback:** deleting the `pending_wakes`
key from a pre-Phase-1 `releases/state.json` after a Phase 1 arm
has run. The next arm's `arm_pending_wake` call would re-create
the key (default `{}` in `ensure_extensions`); the rollback is
ineffective. The correct rollback is the code revert, not a
state.json edit.

---

## Exit Criterion

**All of the following, objectively verifiable:**

1. **Schema round-trip:** T1.1 + T1.2 GREEN; a `PendingWake`
   round-trips through `from_json` / `to_json` byte-exact;
   garbage-tolerant on `null` / `[]` / `"<str>"` / `123`.
2. **Atomicity:** T1.3 GREEN; `arm_pending_wake` and
   `write_pending_op` produce a single `journal_write` (verified
   by `os.replace` spy) under the SAME caller-acquired journal
   lock (`journal_write` has no internal lock — serialization is
   the caller's lock, architecture delta #3); a simulated crash
   leaves EITHER the pre-arm state OR the post-arm state — never
   a half-state. T12's placement check is GREEN (call INSIDE the
   lock-holding try at both arm sites).
3. **State machine:** T1.4, T1.5, T1.6 GREEN; the four
   `mark_wake_*` helpers transition exactly as documented;
   `mark_wake_delivered` and `mark_wake_abandoned` remove the
   record from the dict (idempotency key).
4. **Terminal-state walker:** T4.1, T4.2, T4.3 GREEN;
   `latest_matching_event` returns the event name / None / None
   on the three cases; the walker is parameterized (no constant
   grounding) — the WAKE event-set lands in Phase 2 as the
   sibling constant `WAKE_TERMINAL_EVENTS` (architecture delta
   #1; `_TERMINAL_EVENTS` itself is never mutated).
5. **Non-interference:** T4.4, T4.5, T4.6 GREEN; `clear_pending_op`,
   the simulated `restart.sh` terminal-clearing sequence, and
   `reconcile_pending_op` do NOT touch `pending_wakes`.
6. **Live-outright-refusal:** T5.12 GREEN; a `system_restart` on
   `self_env=live` returns the live-outright-refusal and writes
   NO `pending_wakes` record (and no `pending_op` either — the
   existing test already covers that, the new assertion is the
   wake record is also absent).
7. **Real-journal-shape pin (r4 fold C1):** T6.7 GREEN; the
   test fixtures use the real `{"ts", "event", "detail"}`
   shape — a fixture using the fictional `{"name", "run_id"}`
   shape is a fail-loud import error. This is the r4 fold C1
   acceptance gate for the journal-side test pack.
8. **Arm integration:** T8 + T9 acceptance verifiable on demo;
   a `system_restart(target=demo)` and a
   `system_upgrade(target=demo, target_version=vX.Y.Z)` arm
   produce the documented `pending_wakes` record.
9. **Pack-mapped validation (r4 fold W3):** the test pack is
   invoked via `bash test/packs/post_restart_arm_notify_journal_unit_test.sh`
   (per `.agents/tester/rules/ensure.md` Core #1 PACK-MAPPED
   discipline), NOT a bare `pytest` invocation. The pack is
   registered in `.agents/tester/PACKS.md` in the
   implementation-lane commit (`phase3-plan.md` T13).
10. **Non-regression:** T11 GREEN; the existing
    `tests/unit/tools/test_upgrade_journal.py` (53-line pack) +
    `tests/unit/tools/test_upgrade_tools.py` (135-test pack) +
    `tests/test_release_journal.sh` all pass byte-exact.
11. **Live untouched:** no live pid verified-touched (sandbox-only
    work + demo pids verified at the demo acceptance step).

The Phase 1 commit is mergeable when 1–11 are green.
Phase 2 starts on the same `feature/post-restart-arm-notify`
branch; the commit that closes Phase 2 is the one that makes the
wake deliverable end-to-end.
