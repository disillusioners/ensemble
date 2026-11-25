# Phase-2 Pilot Report — OrphanWatcher under the central engine

**For:** the Emperor · **Date:** 2026-11-23 · **Lane:** `dev-coder-engine-p2-ow` (EVIDENCE lane) · **Branch:** `feature/engine-pilot-orphan-watcher` (base `7243143d0`) · **Design artifact:** `phase2-design.md` (this directory)

> ## THE ONE THING TO READ FIRST
>
> **The migration seam was ALREADY LANDED — and it is already LIVE and
> DORMANT. This lane did not build it and does not claim it.**
>
> Live runs **v0.31.42** (`source_commit=ea5325a60ed3c7c04b32a3eeeaa5dea83565bf26`),
> and the frozen binary's PYZ was carved and byte-probed: it contains
> `engine_owns_execution`, `build_engine_executor`, `rearm_legacy_loop`,
> `SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE` and `ORPHAN_WATCHER_ENGINE_FLAG`.
> The pilot flag family is **unset in production**, so production still runs
> the legacy loop — exactly as intended.
>
> **What this lane's staged release carries is the EVIDENCE and the
> MACHINERY (a runtime parity proof, the ownership truth table, a soak
> harness, a boot-SLO tool, and two honesty fixes) — not the migration.**
> While the flag is OFF, the staged release does **not** change the sweep's
> runtime behaviour at all.

---

## 1. Already-landed vs delivered-by-this-lane

### 1.1 ALREADY LANDED (verified, NOT this lane's work)

| Commit | Date | Subject |
|---|---|---|
| `5044dc148` | 2026-11-21 | fix: OrphanWatcherSweepService born-dead wiring trap — late-bind the bus singleton per tick, LOUD boot wiring assertion, dead-detection escalation |
| `db2e1cb28` | 2026-11-22 | feat: pilot orphan-watcher sweep onto central engine behind per-job flag |
| `4be00db7e` | 2026-11-22 | fix: close fail-stuck paths in pilot dual-path ownership gate |
| `2dd42d9f2` | 2026-11-22 | fix: harden pilot dual-path gate per leader riders R1-R6 |
| `5b562165e` | 2026-11-22 | fix: pin the production bridge failure seam and truthful executor-drop log |

All five are ancestors of this lane's base `7243143d0` (re-verified here via
`git merge-base --is-ancestor`) and are contained in the live `v0.31.42` cut.

### 1.2 DELIVERED BY THIS LANE

| # | Deliverable | Path | What it is |
|---|---|---|---|
| D1 | Design artifact | `.agents/shared/planning/central-polling-engine-study/phase2-design.md` | verification findings, the truth table, the parity design, the soak spec, the flip recipe, the measured boot cost, the risks, the rollback story |
| D2 | This report | `.agents/shared/planning/central-polling-engine-study/pilot-report.md` | the Emperor-facing summary |
| D3 | Honesty fixes (2 files) | `daemon/services/engine/boot.py`, `daemon/api.py` | the migrated spec's description/metadata are truthful; the posture line reports `executable_specs=` |
| D3b | Diff-audit re-anchor (1 file) | `tests/unit/services/test_engine_diff_audit.py` | ONE row for a FOREIGN pre-existing path; `BASE_SHA` unchanged |
| D4 | Parity pack | `test/packs/engine_orphan_pilot_runtime_parity_test.sh` (32 nodes) | the RUNTIME proof (real bus, real SQL) + the truth table + the honesty pins |
| D4b | Runtime parity tests | `tests/unit/services/test_orphan_watcher_runtime_parity.py` (10) | real-bus parity, the loop-affinity measurement, the mutation self-proof, the byte proofs |
| D4c | Truth-table tests | `tests/unit/services/test_orphan_watcher_truth_table.py` (15) | all 8 combinations + the unset/`0` distinction + the same-predicate pin |
| D4d | Honesty pins | `tests/unit/services/test_orphan_watcher_spec_truthfulness.py` (7) | the truthful spec, the untouched 11, the dead-detection safety |
| D5 | Soak harness + doc | `scripts/soak/engine_pilot_orphan_soak.py`, `soak-harness.md` | the read-only 48 h machinery + the exact verdict rule |
| D6 | Boot-SLO tool | `scripts/measure/engine_pilot_boot_slo.sh` | the isolated boot → readyz-green measurement (emits the JSONL the soak harness consumes); `--engine-flag/--shadow-mode` also drive the **flag-ON runtime proof** |

---

## 2. The three findings that decide the flip

### 2.1 The study's dead-lane claim is **REFUTED**

The Phase-0 study said *flag ON + shadow ON ⇒ dead sweep lane*. **It is
false.** The legacy suppression is gated on the FULL conjunct
(`flag ∧ engine enabled ∧ ¬shadow`), and the executor attachment uses the
**same** predicate — so the two can never disagree, and with shadow ON the
legacy loop is **not** suppressed. All eight combinations resolve to
**exactly one owner** (no BOTH, no NEITHER); only
`flag ON ∧ shadow OFF ∧ engine enabled` gives the engine ownership.

### 2.2 The REAL dead-lane risk — the loop-affinity one — is **ABSENT** (measured)

The legacy path awaits the bus sweep on the **main loop**; the engine path
runs the same method via `asyncio.run(...)` **inside a worker thread** — a
**different event loop**. Measured against a **REAL** `DependencyBus` (the
landed unit pack uses a shape-mock, so it could not see this):

* the two paths were observed running on **different loops in different
  threads**;
* the sweep's call graph touches **no** loop-bound primitive (only
  `asyncio.to_thread`, which binds to the caller's running loop);
* with the bus's `asyncio.Lock` **bound to the main loop AND held**, the
  engine path **still completed** correctly;
* a **mutation** (a sweep that acquires that lock) makes the harness
  **fail** — so the harness has teeth, and the absence of the failure is
  evidence, not silence.

**Nothing blocking was found in the landed pilot code.**

### 2.3 The last missing artifact — the RUNTIME proof at flag-ON — is now MEASURED

A dev boot on the **fenced dev DB** with the pilot switches set (a dev
boot, **not** a live flip) produced the end-to-end evidence:

```
OrphanWatcherSweepService legacy loop SUPPRESSED — engine owns execution
  (SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1, engine enabled, shadow off)
orphan-watcher pilot: engine executor registered, legacy loop suppressed
engine boot posture: ... use_engine_flags=SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1 (1/12);
  executable_specs=1 (orphan-watcher); block_elapsed_ms_gated=144.249
```

```
engine_runs id=687  job_id='orphan-watcher'  outcome='SUCCEEDED'
   started_at=2026-11-23T18:18:29.857842Z  finished_at=...29.868268Z
   duration_seconds=0.010382  error_class=None
engine_jobs['orphan-watcher']: last_outcome='SUCCEEDED'  dead=0
```

| Check | Result |
|---|---|
| the legacy loop ticked during the flag-ON boot | **0** ⇒ exactly one owner |
| cross-loop errors / `Traceback` / `FAILED_BACKOFF` | **0** |
| `engine_jobs.dead` | **0** (no false-dead) |
| the sweep's cancellations | **0 orphans existed** in the dev DB — a real sweep that found nothing (stated explicitly, not implied) |

⇒ **Truth-table row 3 is empirically confirmed end-to-end**: flag ON ∧ shadow
OFF ∧ engine enabled ⇒ the engine executes the sweep, the legacy loop is
suppressed, the run is stamped `SUCCEEDED`, and the engine path — running on
its worker-thread loop in a live daemon — raised nothing.

---

## 3. The flip-precondition number (adjudicated)
A shipped flipprep boot measured `block_elapsed_ms_gated=499.918 ms`
against the SLO-1 ≤250 ms cost bound. **This lane measured the landed engine
boot block on this tree four times** (two via its own D6 script, two via the
sanctioned pack `engine_boot_slo_gate.sh` LEG 3, which runs the same fenced
boot independently):

| Boot | Producer | `block_elapsed_ms_gated` | `block_elapsed_ms_total` | launch→readyz (**UPPER BOUND**) |
|---|---|---|---|---|
| run 1 | this lane's D6 script | **142.808 ms** | 161.710 ms | **28.525 s** |
| run 2 | this lane's D6 script | **89.332 ms** | 105.086 ms | **24.361 s** |
| run 3 | sanctioned pack LEG 3 | **106.228 ms** | — | **26.540 s** |
| run 4 | sanctioned pack LEG 3 (post-commit) | **112.560 ms** | — | **22.415 s** |
| run 5 | this lane's D6 script (fix turn) | **105.052 ms** | 123.575 ms | **24.385 s** |

Every one is **inside** the 250 ms WARM bound (headroom 1.75×–2.80×) and
inside the 360 ms COLD bound. The flipprep 499.918 ms breaches **both**
bounds and was therefore not produced by this tree in this posture (out-of-
class or a different tree). **The flip decision should not be priced against
499.918 ms.**

All four boots are also inside the Emperor's binding boot SLO
**22.4 s – 28.5 s — launch→readyz, an UPPER BOUND (F3 relabel, review #7).**

**The Emperor's SLO is boot_epoch → readyz-green.** The harness's stopwatch
runs from the moment it LAUNCHED the daemon, and the daemon captures its boot
epoch *after* launch — so the launch figure is an **upper bound** on the SLO
measure, and a verdict taken on it is conservative. From run 5 the D6 script
reports **both**, distinctly labelled:

| Number | Meaning | run 5 |
|---|---|---|
| `daemon_reported_boot_to_readyz_seconds` | **SLO-ANCHORED** (the Emperor's measure): the daemon's `boot_epoch` → the readiness composite's own `checked_at` | **9.000 s** |
| `daemon_uptime_at_readyz_seconds` | the daemon's own process uptime at green (corroboration) | 8.921 s |
| `launch_to_readyz_seconds` | **UPPER BOUND** (includes pre-boot-epoch construction/import time) | 24.385 s |

⇒ the only SLO-anchored measurement so far is **9.000 s**; the earlier
22.4–28.5 s figures are upper bounds and are relabelled as such everywhere
they appear. Both are within the 120 s SLO.

Re-derivable with:
`bash scripts/measure/engine_pilot_boot_slo.sh --source <tree> --out measurements.jsonl`

---

## 4. FLIP CEREMONY SAFETY

### 4.1 The exact flip recipe

```
# in the daemon's environment, then RESTART the daemon
SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1
ENSEMBLE_ENGINE_SHADOW_MODE=0
# ENSEMBLE_ENGINE_ENABLED=1 is the shipped default — omit it
```

The ownership decision is read at **BOOT** (both api.py sites), so the
flag change takes effect at the **next restart**, not at runtime.

### 4.2 Pre-flip preconditions

1. `bash test/packs/engine_orphan_pilot_runtime_parity_test.sh` → **PASS
   32/32** (this tree).
2. `bash test/packs/engine_orphan_pilot_parity_test.sh` (the landed pack) →
   **PASS** (adjacent pin; re-run at the ceremony).
3. `bash test/packs/engine_diff_audit_test.sh` → **PASS** (Leg E; the map
   carries this lane's rows; `BASE_SHA` unchanged).
4. `bash scripts/measure/engine_pilot_boot_slo.sh --release <release-dir>`
   → the release's own gated block ≤ 250 ms and the **SLO-anchored**
     boot_epoch→readyz ≤ 120 s (the D6 script now reports that number
     explicitly, next to the launch→readyz upper bound).
5. The staged release's binary carries the **honesty fixes** (the truthful
   spec description and the `executable_specs=` posture field) — the
   ceremony's evidence line must not understate the posture.
6. The soak harness must be runnable **read-only** against prod with the
   `ensemble_prod_ro` role, and its verdict rule agreed in advance.

### 4.3 Rollback — **BOOT-SCOPED**

* **Rollback = set `SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=0` (or restore
  shadow mode) and RESTART.** A *runtime* flag flip does **not** re-arm the
  legacy loop (the suppression is armed at boot), so a runtime trip would
  leave the lane owned by NOBODY until the next restart — hence "a kill is
  a restart with the flag off".
* The boot-block failure path re-arms the legacy loop automatically
  (fail-to-legacy, never a boot abort) — verified by the landed pack's
  legs and re-pinned by this lane's truth-table work.
* Both flags are independent: shadow ON ⇒ the engine observes only
  (`SKIPPED_SHADOW`) and the legacy loop keeps running.

### 4.4 ⚠ The soak harness needs the FLIPPED release (F4, review #9)

**The harness's R7 ownership rule reads this lane's `executable_specs=`
field from the daemon's boot posture line.** A **pre-promote** daemon (an
older binary without the field) gives R7 no ownership evidence ⇒ the
harness degrades to **INCONCLUSIVE** (never a false VIOLATION).

⇒ **Run the soak harness only against the FLIPPED release**, and expect
INCONCLUSIVE if it is pointed at anything older. The harness names the
shape it saw (`pre-promote posture line (no executable_specs field)`).

### 4.5 Residual risks (with their evidence)

| Residual risk | Severity | Evidence | Mitigation |
|---|---|---|---|
| A runtime flag trip leaves the lane owned by NOBODY | **HIGH** (operational) | `killswitch.py:16-22`; `execution.py:306-314` | boot-scoped rollback only; ceremony documents it |
| The ceremony misreads ownership from the skip stamp | MEDIUM | `SKIPPED_SHADOW` cannot distinguish shadow-on from no-executor (`execution.py:438-440`); measured: at the shipped posture the orphan job stamps `SKIPPED_SHADOW` like the other 11 | use the posture line's `executable_specs=` + SUCCEEDED rows; R7 in the soak harness |
| The engine boot block regresses past the cost bound | MEDIUM | not observed (89–143 ms vs 250 ms) | D6 re-measurement at flip time |
| The boot SLO regresses | MEDIUM | not observed — SLO-anchored **9.0 s**; launch→readyz upper bound 22.4–28.5 s (vs 120 s) | R9 in the soak harness; D6 per boot |
| A false `dead=true` on the live lane from the metadata honesty fix | HIGH (if mishandled) | the CS-9 carve-out keys off the **executors dict**, not the metadata (`metrics.py:69`) — pinned by test | none needed; pinned + live-observed `dead=0` |
| The soak harness is run with write credentials | HIGH | — | the harness pins `default_transaction_read_only = on`; use `ensemble_prod_ro` |

---

## 5. The soak harness — usage

```bash
# READ-ONLY (prod: the ensemble_prod_ro role — never the daemon's credentials)
scripts/soak/engine_pilot_orphan_soak.py \
    --dsn "postgresql+psycopg://ensemble_prod_ro:***@127.0.0.1:5432/ensemble_prod" \
    --window-hours 48 \
    --log-dir /var/log/ensemble \
    --boot-telemetry boot-measurements.jsonl \
    --json-out soak-report.json
```

* Exit **0** = PASS · **3** = VIOLATION · **4** = INCONCLUSIVE.
* The **48 h clock starts at the flip ceremony** — this harness builds and
  validates the machinery; it does not start the clock.
* **MISSING DATA ⇒ INCONCLUSIVE, never a silent PASS.** The report names
  exactly which evidence is missing.
* `scripts/measure/engine_pilot_boot_slo.sh` produces the
  `boot-measurements.jsonl` (one JSONL record per boot) that feeds the
  harness's boot-SLO rule.

The harness was validated end-to-end against the fenced dev DB: it returns
**INCONCLUSIVE** there (correctly — the dev DB has no continuous engine
evidence), with R3/R4/R5/R6/R8/R9 PASS and the missing evidence named.

---

## 6. Parity evidence paths

| Evidence | Path |
|---|---|
| The runtime parity pack | `test/packs/engine_orphan_pilot_runtime_parity_test.sh` — `RESULT: PASS`, **32/32 nodes** |
| The runtime proof (real bus) | `tests/unit/services/test_orphan_watcher_runtime_parity.py` — 10 nodes |
| The truth table | `tests/unit/services/test_orphan_watcher_truth_table.py` — 15 nodes |
| The honesty pins | `tests/unit/services/test_orphan_watcher_spec_truthfulness.py` — 7 nodes |
| The landed pack (adjacent, re-run) | `test/packs/engine_orphan_pilot_parity_test.sh` — 59/59 at `5b562165e` |
| The byte proofs | `git diff 5044dc148..HEAD -- daemon/services/dependency_bus.py` → EMPTY; `... -- daemon/services/orphan_watcher_sweep.py` → 181 insertions / **0 deletions** |
| The flag-ON runtime proof (in situ, fenced dev DB) | `bash scripts/measure/engine_pilot_boot_slo.sh --source <tree> --engine-flag 1 --shadow-mode 0 --timeout 60 --out flagon.jsonl` → `engine_runs id=687 outcome='SUCCEEDED' duration=0.010382s`; legacy loop SUPPRESSED (0 ticks); 0 cross-loop errors; `dead=0` |
| The boot-SLO measurement | `scripts/measure/engine_pilot_boot_slo.sh` → one JSONL record per boot (`block_elapsed_ms_gated`, `boot_to_readyz_seconds`) |
| The live dev-DB stamps (ownership evidence) | `engine_runs` for `orphan-watcher`: 44 `SKIPPED_SHADOW` + 4 `SKIPPED_KILL_SWITCHED`; `engine_jobs`: `last_outcome=SKIPPED_SHADOW`, `dead=0` |

---

## 7. Staged release pointer

| Field | Value |
|---|---|
| Staged version | **v0.31.49** — re-derived at cut time (2026-11-24 ~01:00Z): 0.31.46/47/48 all taken by concurrent lanes; triple-bump 0.31.46→49, intermediate bump commits dfd49b110 (0.31.47) + 27f13f756 (0.31.48) ride latest untagged |
| Staged commit sha | **ac34da1d234fd53f23548529088a4a36ac16f408** (bump commit `! New version 0.31.49`, on latest, pushed) |
| Annotated tag | **v0.31.49 → tag object 6c35d35445f111f45d332616837d472e4b566455, peeled commit ac34da1d2** (tag == latest tip, zero cut-point ambiguity) |
| 4-way artifact sha | **528e089539f690e9d5bcb7f48f8a6db4fc36c551834d7fbcbaddbc07ec9577e5** — independently re-hashed by giter: release-dir binary == stage-wt dist == manifest.binary_sha256 == sidecar artifact_sha256, all four legs identical |
| staged_at | **2026-11-24T01:17:05Z** (rollback_safe=true source=explicit; known_schema_gen=20261121_120001_create_engine_runs.sql; NO flip — current stays v0.31.42) |

This lane performed **no** promote, **no** release staging, **no** tag
cutting, **no** merge, **no** push, **no** live flag flip, **no** daemon
restart, and **no** write to the live install or the prod DB. All DB work
was against the fenced dev DB (`ensemble_dev`) or a temporary SQLite file.

---

## 8. The three states, explicitly

* **(i) Already-landed and verified** — the whole pilot seam (5 commits,
  live and dormant in v0.31.42). Verified here with code citations and
  tests; **not rebuilt, not re-implemented, not claimed**.
* **(ii) Fixed by this lane** — the migrated spec's truthful
  description/metadata; the posture line's `executable_specs=` field; the
  diff-audit map's one foreign-path row.
* **(iii) Still missing** — the **ceremony itself** (the flip, the 48 h
  window, the soak verdict) and the **staged release** (the staging lane's
  work). The harness, the packs, the truth table and the boot tool are
  delivered and validated; they are machinery that needs the Emperor's
  ceremony to run against.

---

## 9. Fix turn (review APPROVE-WITH-NOTES, 2026-11-23) — what changed

| # | Review note | Fix | Evidence |
|---|---|---|---|
| **F1** (#5) | the soak harness's server-side RO guard was **INERT** (the connect-event `SET` is defeated by psycopg3's `BEGIN` ordering; the reviewer's probe returned WRITE ALLOWED) | the engine now opens with `connect_args={"options": "-c default_transaction_read_only=on"}` — a **connection-startup option** the server applies before any transaction | probe transcript **AFTER: WRITE REFUSED** (`psycopg.errors.ReadOnlySqlTransaction`), with the BEFORE reproduced on the same host: `.agents/tester/RESULTS/2026-11-23-engine-pilot-ro-guard-probe.txt` |
| **F2** (#6) | the role was never asserted — any DSN was accepted | the harness reads `SELECT current_user` from the live connection and **refuses loudly** when the target is prod-shaped and the role is not `*_ro`/`ro`-shaped | the same transcript's third leg: prod-shaped + `ensemble_prod_writer` ⇒ **REFUSED LOUDLY** |
| **F3** (#7) | the boot figure was clocked launch→readyz but labelled boot_epoch→readyz | the D6 script now reports **BOTH**, distinctly labelled: `daemon_reported_boot_to_readyz_seconds` (**SLO-anchored**, 9.000 s) and `launch_to_readyz_seconds` (**UPPER BOUND**, 24.385 s), plus the daemon's own `daemon_uptime_at_readyz_seconds` (8.921 s) as corroboration; every 22.4–28.5 s mention is **relabelled as an upper bound** | §3 table (run 5) |
| **F4** (#9) | R7's posture rule needs this lane's `executable_specs=` field | the harness now parses the **pre-promote** posture shape and degrades R7 to **INCONCLUSIVE** (never a false VIOLATION); the consequence is documented in `soak-harness.md` and §4.4 here | observed live: the harness reports `pre-promote posture line (no executable_specs field)` |
| **F5** (#1) | `.f2gate-transcripts/` was untracked, so "tree clean" was tracked-scope only | **gitignored** at the repo ROOT only (`/.f2gate-transcripts/`) — a tight leaf; the path is the sanctioned pack's own output directory, and relocating it would mean editing a pack this lane does not own | `git check-ignore -v` confirms; the tree is now genuinely clean |

**Deferred (non-blocking, rides the leader's report):** #8 the polarity
assertion, #4 the public accessor, #10 the JSON escaping — **not landed**
here, by instruction.

---

## 10. Disclosures — pre-existing conditions and lane drift

**9.1 A PRE-EXISTING RED in the observability pack (NOT caused by this
lane).** `test/packs/engine_observability_unit_test.sh` fails at
`tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files`
with 14 daemon/ offenders (the same drift paths the diff-audit map's stage-2
addendum carries: `daemon/constants.py`, `daemon/repositories/event/models.py`,
`daemon/repositories/report_injection/repository.py`,
`daemon/repositories/source/maintenance_seed.py`,
`daemon/repositories/task/repository.py`, `daemon/routers/maintenance_reports.py`,
`daemon/services/live_views.py`, `daemon/services/maintenance_schedule_knobs.py`,
`daemon/services/pok_service.py`, `daemon/services/readiness.py`,
`daemon/services/waiting_children_watchdog.py`,
`daemon/tests/test_inject_allowed_models.py`, `daemon/tools/project_future.py`,
`daemon/tools/project_history.py`).

**Proved pre-existing:** with this lane's four modified files **stashed**
(explicit-path stash) the leg fails with a **byte-identical** offender list;
after committing, the list is unchanged and **does not contain this lane's
two daemon/ paths** (`daemon/api.py`, `daemon/services/engine/boot.py`).
The pin belongs to the metrics-family lane; its re-anchor is that lane's
call, not this lane's.

**9.2 Base drift.** This branch is pinned to `7243143d0` (the `v0.31.44` tag
commit) by the commission. `origin/latest` advanced **during** this lane
(11 → 27 commits ahead; the tip moved from `51dc98bb7` further still) —
**forward drift, no divergence signal**. This lane did **not** pull, merge,
rebase or fast-forward; the merge lane owns the finalise and must re-anchor
per the house FINALIZE rule (fetch + verify `latest` unmoved from the
branch base; if it moved, STOP and re-anchor).

**9.3 Contended path.** `.agents/tester/PACKS.md` was also touched by the
concurrent release-retention lane. This lane's registration block was
inserted at the **top** of the file (the house newest-first convention) —
far from the retention lane's end-of-file append — so the two regions do not
overlap textually.

---

## POST-STAGE EVENTS — 2026-11-24

* **(a) Promoted LIVE.** 2026-11-24T02:56:25Z — v0.31.49 promoted live by **operator ceremony** (run_id `r-20261124-024726-0080`; NOT this lane). The promote does **not** flip the kill-switch: live runs **DORMANT** (flags unset; the legacy path owns the sweep; the engine observes).
* **(b) Remaining ceremony step = the FLAG FLIP.** Set `SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1` AND `ENSEMBLE_ENGINE_SHADOW_MODE=0` (engine enabled) + daemon restart. **Rollback is BOOT-SCOPED** — restart with the flag off.
* **(c) Soak clock.** The 48h soak clock **starts at the FLIP, not at promote**.
* **(d) Harness shipment.** The soak/boot tools are **not** in the release payload — run them from a checkout at tag `v0.31.49`. The stage worktree `/home/nea/ensemble-src-wt-stage-v03149-enginep2` **is** that checkout and is **KEPT** for the ceremony.
* **(e) Staged-binary proof.** The fenced proof (SUCCEEDED rows, truth table, SLO numbers) lives at `.agents/tester/RESULTS/2026-11-24-engine-p2-ow-staged-binary-verification.md` (18,616 B; merged to latest by the leader sweep, commit `d58f5ace1`).
