# Test Strategy: Upgrade-System Overhaul (fast-by-default, reliable promote ceremonies)

Date: 2026-10-12 (authored in worktree /home/nea/ensemble-src-wt-upg-overhaul, branch `feature/upgrade-speed-overhaul`, HEAD `c8a3fbf6`)
Author: worker (test-strategy lane) for the upgrade-system overhaul mission
Status: Draft — keyed to WORKSTREAMS G1–G6 + AC1–AC5; decision-coupled points are parameterized (marked **P-Dx**) because decisions (a)–(g) are being authored in parallel in `decisions.md`.

---

## 0. Ground Rules (bind every test in this document)

1. **Fixture-only execution.** Every test runs against a throwaway `INSTALL_DIR` (mktemp), reusing the `journal_init` / raw-`printf > state.json` / sed-splice / python3-heredoc fixture styles already proven in `tests/test_release_journal.sh` and `tests/test_atomic_flip.sh`. **Never** the live `INSTALL_DIR` `/home/nea/agents-ensemble`, never `ensemble-main.service`, never `systemctl` against real units, no network, no real ports (the packs already run daemon-free; this preserves that).
2. **Read-only w.r.t. code during strategy authoring**; when tests are implemented they live only in `tests/**` on the mission branch.
3. **No new frameworks.** Bash packs stay plain self-asserting bash (no bats); Python stays pytest with `tmp_path` fixtures. Reuse existing primitives: `iso_off` (`tests/test_atomic_flip.sh:89`), `uname` stub for the BSD branch, per-subtest `INSTALL_DIR` override, scrubbed-env bash subprocess interop (`tests/unit/tools/test_upgrade_journal.py` precedent).
4. **Parameterize decision flips.** Where decisions (a)–(g) could flip, tests assert BOTH options and are tagged **P-Dx** so the implementer resolves the matrix rather than silently picking one.
5. **Pin-before-change.** Characterization tests for every hard constraint (§5) are written against current HEAD behavior FIRST, so refactors cannot silently weaken gates.

---

## 1. AC → Test Matrix

| AC | Named test | Layer | File | Pass criterion |
|----|-----------|-------|------|----------------|
| **AC1** journal event write <1s @ 100KB/500-entry | `T-AC1a splice-latency` | TIMING | `tests/test_journal_splice.sh` (latency section) | Median-of-5 journal event writes (new splice path) on BOTH fixtures (100KB/500-entry, 87KB/420-entry real-shape) **< 1.0s hard**; max-of-5 < 2.5s (warn-only headroom tripwire). Baseline run on pre-change HEAD records ~139s/read, ~280s/event for the before/after delta report. |
| AC1 | `T-AC1b large-doc-read-integrity` | bash | `tests/test_journal_splice.sh` | On the 500-entry fixture: full read via new path returns valid JSON (`journal_read`-equivalent), and byte-identity of all UNTOUCHED regions after a splice (only the mutated field/entry diff vs. pre-image). |
| **AC2** clean promote ≤10min | `T-AC2a stub-ceremony-wallclock` | TIMING | `tests/test_ceremony_wallclock.sh` | Stub ceremony in throwaway install dir, typical-phase profile (see arithmetic below), soak ∈ {60s, 120s} (**P-D2** both options): wall-clock `armed_at → commit` parsed from ceremony journal history **≤ 600s** per matrix cell. **Q6a final:** ≤600s claim is RE-SCOPED to the TYPICAL path (4.7–5.7 min observed, ≤2x margin). |
| AC2 | `T-AC2b budget-sum identity` | bash (config-resolution) | `tests/test_ceremony_wallclock.sh` (budget section) | Asserts the resolved per-phase budget sum + default soak against a checked-in identity table; FAILS if any future change raises a phase budget without the table being consciously updated. **C-10a reconciled arithmetic:** fixed 42 s (preflight 35 + flip 1 + version 5 + commit ~1) + variable ceilings (stop default eff 225 s — worst 755 s — + handback 75 + livez 180 + readyz 120). With soak=60 s default: **non-stop worst 870 s + 30 s slack inside 900 s ceiling**; all-budgets-max ceiling (stop worst 755 + every budget max) = **1400 s — abort-path, breached → halt**. The 885 s value in decisions.md (d) is RECONCILED with 870 s + 30 s slack (different stop-worst walks). AC2 is therefore defined on (a) the typical-path drill ≤600 s (TYPICAL path) and (b) this identity table keeping drift visible. |
| AC2 | `T-AC2c arithmetic evidence` | doc-check | this file §4c + drill output | Arithmetic recorded: today 19 append sites × ~280 s/event ≈ **28–46 min journal-dominated** at 87 KB (139 s/read × ≥2 reads per update, ×N events). Post-splice: ≤ 19 s total journal cost. E6 non-journal budgets 536 s; typical observed non-journal ≈ 221 s. With adaptive soak 60–120 s: **typical 4.7–5.7 min (≤ 10 min with ~2x margin)**; **non-stop worst 870 s + 30 s slack inside 900 s ceiling**; all-budgets-max ceiling 1400 s (halt). E6 clean design budget 8–13 min at 300 s soak reconciles (536+300 ≈ 836 s ≈ 13 min). |
| **AC3** conservative ≈ current envelope | `T-AC3a conservative-config-resolution` | bash/pytest | `tests/test_adaptive_soak.sh` (resolution section) | `ENSEMBLE_PROMOTE_SOAK_S=300` + all budget knobs explicit resolves to the EXACT current tuple (soak 300, budgets :78-79/:94-97 unchanged, fail-closed flags identical). Asserted as a config-resolution equality test. |
| AC3 | `T-AC3b conservative golden sequence` | bash | `tests/test_adaptive_soak.sh` | Stub ceremony in conservative mode emits a journal event sequence **byte-equivalent (modulo timestamps/ids)** to a golden fixture recorded from current HEAD behavior. |
| AC3 | `T-AC3c fail-closed bit-identical` | bash | `tests/test_journal_splice.sh` + §5 pins | Exit-78 matrix (§5) green and UNCHANGED in conservative mode. |
| **AC4** mis-sweep impossible while executor alive | `T-AC4a executor-alive-liveness` | pytest | NEW `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` | **C-9 fixture fix**: the in_flight heartbeat is the field the splice writes — the fixture MUST construct producer-side state via the splice path (`_splice_journal set-in-flight-field last_heartbeat "$now"` writes to `state.json`, then the sweep reads). The prior fixture asserted a first-ever writer for `owner_heartbeat_at` (= production-impossible, since `pending_op` is owned by the python reconciler and the helper MUST NOT touch `pending_op` per D-P2.2). The AC4 proof is real: FRESH `in_flight.last_heartbeat` (via splice path) + STALE `armed_at` (> `EXECUTOR_STALENESS_WINDOW_S=900 s`) + LIVE pid (real sleeping subprocess, `os.kill`-targetable) → `_executor_alive_recent` returns True; reconcile does NOT clear pending_op; no failure-state write. Sweep constructed via the constructor-kwargs test seam (tiny intervals). |
| AC4 | `T-AC4b defense-in-depth` | pytest | same file | (i) unparseable heartbeat ts + pid present → True (pins :1592-95); (ii) lock-held defer: pipeline lock held → launcher sweep DEFERS (return 0, no mutation) — extends `tests/test_launcher.sh` / launcher :913-936; (iii) fresh heartbeat alone (stale armed_at, no pid) → True. |
| AC4 | `T-AC4c mis-sweep control (negative)` | pytest | same file | STALE armed_at + no heartbeat + dead pid → sweep proceeds (control cell proving the guard itself is what protects, not a vacuous always-True). |
| **AC5** rollback targets N-1 always | `T-AC5a rollback-target matrix` | bash | NEW `tests/test_rollback_target.sh` | Four cells, each asserts `previous == N-1` at every commit/rollback boundary: (1) fresh journal; (2) **halt→re-promote chain (Oct 9 arc)**: attempt-1 gate-fail → auto-rollback to N-1; attempt-2 flip-from-rolled-back; commit → previous==N-1; (3) **`boot_sweep_commit_and_continue` commit → previous==N-1 sourced from `in_flight.displaced_version` (stamped at `journal_open_txn`, `lib.sh:700-712` — C-4), NOT `journal.previous` (which would land N-2); pack MUST also cover the old-format-`in_flight` (no `displaced_version` field) → halt-for-human branch as part of this cell's pass criteria** (extends `tests/test_boot_sweep_commit_and_continue.sh`); (4) double-rollback → target stays N-1, no N-2 walk. |
| AC5 | `T-AC5b previous-site pins` | bash grep-assert | `tests/test_rollback_target.sh` | promote.sh commit :438-441 writes `previous=${OLD_CUR}`; auto-rollback :452-453 reads **commit-time-recorded N-1** (not a stale journal `previous` — the N-2 bug), with read-time validation; rollback.sh sites :70-77/:82/:108-113/:212 pin-consistent. |
| AC5 | `T-AC5c immediacy-vs-legitimacy` | bash | `tests/test_rollback_target.sh` | Validation too strict must NOT halt-for-human on legitimate flows: matrix cell 2 completes automatically (no human-halt event) despite the halt in the middle. |

---

## 2. Goal → Test Mapping (G1–G6)

| Goal | Named tests | File(s) | Pass criterion |
|------|------------|---------|----------------|
| **G1** Stall elimination (bounded retries + backoff, per-phase deadlines, total budget, fail-closed breach) | `T-G1a bounded-retry pins` — extend `tests/test_bounded_subprocess_waits.sh` (existing 7 bounded sites + §9 hang repro) with every NEW retry/backoff site: bounded return within budget, backoff sequence asserted, halt event appended on exhaustion (NOT infinite hang). `T-G1b deadline-breach routing` — stub ceremony with sleep-injected phase overrun: journal shows `gate_failed → rollback_started` (or halt) events; **no gate-pass event** (breach routes to rollback/halt machinery, never gate-skip). `T-G1c total-budget invariants` — (i) pin `total_ceremony_budget < EXECUTOR_STALENESS_WINDOW_S (900s)` so breach→rollback completes inside the sweep's stale window (no race); (ii) lock-wait accounting explicit (**P-D1**: parameterize wait-counts-in vs wait-counts-out; both options tested); (iii) livelock probe: budget expiry fires even while a lock is contended (bounded wait, then fail-closed). `T-G1d` — regression-pin existing handback bounds (:94-97). | `tests/test_bounded_subprocess_waits.sh` (ext), `tests/test_ceremony_wallclock.sh`, `tests/test_rollback_target.sh` | All breaches terminal within budget; no unbounded wait introduced; invariants pinned. |
| **G2** Adaptive soak 60–120s, N-green; 300s conservative opt-in | `T-G2a soak choreography` — NEW `tests/test_adaptive_soak.sh`: N-consecutive-green early-exit math at **15 s cadence** (NOT 30 s — leader Q6a final; N=2→≥45 s, N=4→≥105 s; N parameterized; **C-1 pinned subtest** `T-G2a-i`: cap-reach with `consecutive_green=0` under simulated sustained transient storm (every probe returns http_code 503) → exit 1, NOT exit 0 → auto-rollback); any probe fail resets green counter; version drift mid-soak fails (:4258 pin); hard cap 900 unchanged (:156-157). `T-G2b conservative = 300s, no early exit` (**P-D2**: if conservative keeps early-exit, test the longer N instead). `T-G2c busy-amnesty asymmetry` (**P-Dx**): readyz has busy-amnesty today, soak/livez none — extension reads the **EXISTING** `readiness.py:198/222 detail.inflight_turns` field via a readyz curl (C-8b; NO new `/run/ensemble/inflight_turns` file; NO new daemon-side writer); the bound (`BUSY_INFLIGHT_BOUND=5`) keeps amnesties genuinely narrow; wedged-forever scenario fails livez/soak after grace. | `tests/test_adaptive_soak.sh` | Choreography green across N ∈ {2,3,4}; cap-reach-with-zero-greens exit 1 proven (C-1); conservative opt-in exact; bounded amnesty proven; existing-readiness C-8b proven (regression pin: zero hits for `/run/ensemble/inflight_turns` in the repo). |
| **G3** Rollback correctness (N-1; stale-previous fix across halt→re-promote + boot_sweep) | `T-AC5a/b/c` (matrix above) + `T-G3a rollback.sh site pins` (:70-77/:82/:108-113/:212) + `T-G3b launcher heal pins` (kill-window promote :332-341 / launcher heal :939-965 regression). | `tests/test_rollback_target.sh`, `tests/test_boot_sweep_commit_and_continue.sh` (ext), `tests/test_atomic_flip.sh` (ext) | N-1 invariant across all four matrix cells; stale-previous (N-2) construction shown impossible post-fix. |
| **G4** Busy-daemon path (audit after soak; boot-window sentinel grace; amnesty asymmetry) | `T-G4a env-poison ordering` (**P-Dx** ordering pre- vs post-commit, both tested): wipe stays idempotent-by-predicate; doomed-capture + audit inside `clear_all` intact (`daemon/manager.py:843` wipe; queue repo :1026+/:1136-1143; boot_epoch pre-wipe :859-873; post-init api.py:424-426). `T-G4b crash-window drill` — SIGKILL between flip and deferred wipe → next boot's pre-wipe cleans (idempotency across restart). `T-G4c sentinel grace` — boot-window grace bounded; launcher sweep defers while sentinel valid (interop with T-AC4b-ii). | NEW `tests/test_busy_daemon_path.sh` + pytest extension in `tests/unit/tools/test_upgrade_journal.py` style | Both orderings green; crash-window recovers on next boot; amnesty never unbounded. |
| **G5** pending_op / orphan-sweep desync (live executor never misclassified) | `T-AC4a/b/c` + `T-G5a reconcile regression` — existing coverage MUST stay green: terminal closure, in-flight guard :1618-20, expiry+grace, torn no-op, READ-FIRST byte-identical; `T-G5b clear_pending_op closure` (:780-785, expiry closure :1665-1682 nulls without failure state); `T-G5c orphan timing` — orphan lifetime ≈ armed+1200s (expire 600 + grace 600) unchanged or improved, pinned. | `tests/unit/tools/test_upgrade_journal.py` (keep-green + new), `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` (new) | Zero desync constructions found; all existing reconcile coverage green. |
| **G6** printf noise (cosmetic) | `T-G6a output hygiene` — stub ceremony stdout: every line matches `_log`/`_warn` prefixes (golden-prefix check); grep-forbid noise signatures (bare numeric lines from `read -r`, stray `%s` format fragments, `null` dribble). `T-G6b captured-var purity` — spot pins at changed sites: no command-substitution capture contains debug output. | `tests/test_ceremony_wallclock.sh` (hygiene section) or `tests/test_journal_splice.sh` | Zero forbidden signatures in a full stub ceremony run. |

---

## 3. Hard-Constraint Preservation Pins

Each pin is a characterization test written against CURRENT HEAD behavior first; refactors must keep them green bit-for-bit (modulo timestamps/ids).

| Constraint | Named test | Anchor(s) | What is pinned |
|-----------|-----------|-----------|----------------|
| livez/readyz gates | `T-PIN-gates` | `_probe` promote.sh:2523-2536; budgets lib.sh:78-79; `gate_soak` :4240-4266 | curl invocation in `_probe`/`gate_soak` MUST be `curl -sS -o <tmp-body> -w '%{http_code}' --max-time 5` (C-8a); **regression pin: `grep -nF 'curl -fsS' lib.sh` returns zero hits inside probe functions**; any non-2xx red, no transient class on `curl -f` paths; budgets 180/120 s; soak cadence 15 s (NOT 30 s — leader Q6a final) + version-drift fail :4258 |
| auto-rollback | `T-PIN-rollback` | promote.sh :459-496 (ordered halt gates), :553 bookkeeping | T5 recovery never refuses on cap/cooldown; ordered halt-gate sequence preserved |
| 3-factor nonce ceremony | `T-PIN-nonce` | ceremony arm/confirm path | Fabricated/missing nonce → refuse (exit-78 family); unchanged by refactor |
| journal txn integrity | `T-PIN-txn` | `journal_read` :546-584 (brace walk :560), `journal_write` :587-603, `journal_update` :609-666 (:611 re-read), `journal_history_append` :676-687; malformed-in_flight byte-unchanged assertion :368-374 | Txn open/close; torn/malformed → refuse with journal byte-unchanged — including through the NEW splice writer |
| all fail-closed checks | `T-PIN-failclosed` | stage.sh :325-351 (rollback_safe unset+destructive-DDL ⇒ exit-78), :353-360 (version-verify vs `__version__`), :616-618 (quarantine clears ONLY via re-stage); promote.sh :153-157 (SOAK non-digit exit-78; cap 900) | Exit-78 matrix green and unchanged |
| plugin staleness journal-and-proceed | `T-PIN-staleness` | `tests/test_promote_plugin_staleness.sh` (ext) | Still journal+proceed (allow-stale default), never blocking, post-refactor |
| promote-only tool-lane fence | `T-PIN-fence` | `daemon/tools/upgrade_tools.py:2477` (system_upgrade dry_run default TRUE), :2187 (system_restart) | Tool lane stays preview/zero-mutation by default; promote.sh remains the only mutating lane (**P-Dx**: IF a promote.sh dry-run mode is added, it is env-gated + tested refuse-by-default) |
| launcher self-containment | `T-PIN-launcher` | launcher.sh (repo root; no lib.sh source, no python3, SWEEP_STALE_S=600 :61) | grep-assert: launcher gains NO new external deps in the boot path (the python3 splice must NOT enter launcher boot-critical code) |

---

## 4. Test Layers

### (a) Bash packs

**New files** (all plain self-asserting bash, journal_init-style fixtures with exported INSTALL_DIR, mktemp install dirs, no daemon/ports/network):

| File | Covers | Fixture styles reused |
|------|--------|----------------------|
| `tests/test_journal_splice.sh` | AC1 (latency hooks + large-doc integrity), splice torn-JSON refusal, python3-missing exit-78 + byte-unchanged, shell↔python lock interop, format back-compat both directions (§R-02/R-09) | raw `printf > state.json`; sed-splice injection (:1857 pattern); python3-heredoc verification (:330 pattern); malformed byte-unchanged (:368-374 pattern) |
| `tests/test_adaptive_soak.sh` | G2 choreography, AC3 resolution + conservative golden | `iso_off` (:89) for probe timestamps; stub probe sequences |
| `tests/test_rollback_target.sh` | AC5 four-cell matrix, T-AC5b pins, T-G3a | journal_init w/ exported INSTALL_DIR; raw full-document state.json injection; python3-heredoc previous-field verification |
| `tests/test_ceremony_wallclock.sh` | AC2 drill + budget identity table + T-G1b breach routing + G6 hygiene | full stub-ceremony pattern already proven in test_release_journal.sh; per-subtest INSTALL_DIR override |
| `tests/test_busy_daemon_path.sh` | G4 ordering/crash-window/sentinel (optional standalone; may fold into pytest) | python3-heredoc pending_op injection with owner_heartbeat_at |

**Extended files**:

| File | Extension |
|------|-----------|
| `tests/test_release_journal.sh` | txn interop with the python splice writer; large-fixture (500-entry) read sanity; subtest: old-format journal read by new code and vice versa |
| `tests/test_atomic_flip.sh` | flip timing unchanged with splice in the loop; `uname`-stub BSD branch retained |
| `tests/test_boot_sweep_commit_and_continue.sh` | commit-and-continue now WRITES `previous == in_flight.displaced_version` (C-4: `journal_open_txn` at `lib.sh:700-712` stamps `displaced_version = journal.current`; launcher :1020-1037 reads it and writes `previous = displaced_version`); pack must cover BOTH the new write (commit → previous==N-1) AND the old-format-`in_flight` (no `displaced_version` field) → halt-for-human branch (mirror `phase1-plan.md` T1.5 verification column); evidence-check gates (:491) intact |
| `tests/test_bounded_subprocess_waits.sh` | every new bounded retry/backoff site; halt-event-on-exhaustion pins |
| `tests/test_promote_plugin_staleness.sh` | regression: journal-and-proceed preserved |
| `tests/test_launcher.sh` | sweep defer under held lock; self-containment grep-assert |

### (b) Pytest units

| File | Covers |
|------|--------|
| NEW `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` | AC4a/b/c: first fake `owner_heartbeat_at` writer (construct the pending_op model directly — field exists at `upgrade_journal.py:741`); stale armed_at + live pid (real `subprocess.Popen` sleeper); constructor-kwargs seam for tiny sweep intervals; defense-in-depth cells |
| NEW `tests/unit/tools/test_upgrade_previous_semantics.py` | AC5 chain at journal level: halt→re-promote sequence via journal API calls (`open_txn` stamps `in_flight.displaced_version = journal.current` per C-4; `rollback`/`re-arm` continue from it); assert `previous` invariant after each hop — across hops, `journal.previous` and `in_flight.displaced_version` cross-check the FORWARD semantic; double-rollback |
| NEW `tests/unit/tools/test_upgrade_splice_helper.py` | Splice helper: torn JSON refuse; atomic replace under **real SIGKILL** — mirror the existing kill -9 vs looping-child-writer test (test_upgrade_journal.py:218-251, bounded <2s); lock interop both directions (python writer under shell flock; shell writer under python lock); python3-missing refusal (scrubbed-env subprocess precedent); **`set-in-flight-field` round-trip pin** (C-2 — the NESTED write is the load-bearing path); field-ownership matrix enforcement (incl. fenced-out `open-txn` per C-13) |
| KEEP-GREEN `tests/unit/tools/test_upgrade_journal.py` | All 2657 lines stay green (reconcile matrix, stale-lock break + live-owner protection, SIGKILL atomicity); add slow-marked large-fixture read timing |
| KEEP-GREEN `tests/unit/services/test_post_restart_arm_notify_sweep.py`, `tests/unit/tools/test_upgrade_executor_systemd_service.py`, `tests/unit/tools/test_promote_cgroup_survivorship_python.py` | Regression pins |

### (c) TIMING layer (new — none exists today)

1. **Fixture generator** `tests/fixtures/gen_state_json.py` (stdlib-only): parametrized state.json builder — targets (100KB, 500 history entries) AND (87KB, 420 entries, real-shape). Schema mirrors `journal_write` output exactly: `current`, `previous`, `txn{in_flight,...}`, `pending_op`, `quarantined[]`, `history[N]{ts,event,message}`. Includes a shape-parity check against the documented field set. NO fixture is ever read from the live install.
2. **Splice latency measurement** (in `test_journal_splice.sh`): median-of-5 hard <1.0s; max <2.5s warn-only. **CI-noise guard:** the assertion carries ~100x headroom vs the 139s/read baseline, so even a 10x-loaded CI runner cannot flip it; `ENVSIM_FULL=1` gates the full matrix (both fixtures × N=5) in CI; default run = one profile (~seconds). A one-time BASELINE run on pre-change HEAD (gated `ENVSIM_BASELINE=1`, expect ~5-10min for a handful of events at ~280s/event) records the before/after delta for the AC1 evidence report.
3. **Clean-promote wall-clock drill** (`test_ceremony_wallclock.sh`): full stub ceremony in a throwaway install dir — the same pattern `test_release_journal.sh` already uses for complete stub ceremonies. Phase durations injected via env (`SIM_STOP_S`, `SIM_LIVEZ_S`, ...); a `typical` profile preset (stop 30s, handback 30s, livez 60s, readyz 60s, preflight 35s) plus soak ∈ {60,120}. Measurement: `ts(intent/arm event) → ts(commit event)` diff parsed from ceremony journal history via the python3-heredoc verification pattern. Assert ≤600s per cell. **Never against live INSTALL_DIR.**

### (d) Sandbox / demo drill (OPTIONAL, gated, MANUAL — the only layer that touches a real init)

- **Setup:** `mktemp -d` INSTALL_DIR under /tmp; **`systemd-run --user --unit=ens-sandbox-$$` transient unit** (or stub systemd if user session unavailable); isolated port ≠ 9797; `ENSEMBLE_SELF_ENV` unset/sandbox-derived; daemon binaries from the worktree build, never the live release.
- **Uniquely validates what stubs can't:** real systemd handback start-budget variance (§9 culprit), real cgroup survivorship (pairs with `test_promote_cgroup_survivorship_python.py`), boot-window sentinel grace under real unit churn.
- **Explicit NO-touch list:** live `/home/nea/agents-ensemble`; `ensemble-main.service` or any real unit (`systemctl` real-unit calls forbidden); ports 9797/9798; network egress; live releases/state.json.
- **Abort criteria:** any real unit name appearing in `systemctl list-units` output during the drill → abort immediately; drill artifacts wiped post-run.
- **Output:** drill report appended to this planning dir (`drill-report-<date>.md`) with wall-clock numbers per phase — feeds the AC2 evidence and the maiden-promote go/no-go.

---

## 5. Negative / Fail-Closed Tests

| Named test | Scenario | Expected |
|-----------|----------|----------|
| `T-NEG-python3-missing` | Splice invoked with PATH scrubbed of python3 (scrubbed-env subprocess precedent) | Refuse exit **78**; journal **byte-unchanged** (byte-unchanged assertion mirrors :368-374); C-7 preflight probe MUST also reject (probe-OK event NOT journaled) |
| `T-NEG-python3-version-low` (C-7) | Splice invoked with python3 below 3.7 (e.g. `sys.version_info = (3,6,...)` via scrubbed env) | Refuse exit **78**; probe-OK event NOT present in history; journal byte-unchanged |
| `T-NEG-budget-breach` | Sleep-injected phase overrun beyond per-phase deadline AND beyond total budget | Routes to **rollback/halt machinery** (journal: gate_failed → rollback_started / halt events); **never** gate-skip (no gate-pass event present) |
| `T-NEG-conservative-resolution` | AC3a/b/c | Conservative mode resolves to exact current behavior; golden event sequence matches |
| `T-NEG-characterization` | Full §5 pin set (nonce, rollback_safe, quarantine, version-verify, fail-closed matrix) | All green on HEAD BEFORE refactor lands; stay green after — refactors cannot silently weaken gates |
| `T-NEG-splice-torn` | Truncated/torn state.json presented to splice helper | Refuse; no mutation; torn no-op parity with existing reconcile torn no-op |
| `T-NEG-lock-contention` | Splice attempt while shell pipeline lock held (and inverse) | Waits or bails per the defined contract; **no lock bypass** — the protocol boundary is honest in both directions |
| `T-NEG-staleness-race` | **Scope pinned to CONSERVATIVE mode ONLY** (default total=900 s; config-resolution test FAILS the build when configured-budget ≥ 900 under conservative; under default mode the assertion is against the 900 s ceiling as a ≤ relationship, never as ≥ — the prior pin was self-failing under default mode: it pinned total < 900 against a default of exactly 900). |
| `T-NEG-deadline-breach-shape` (NEW, C-10c) | Sleep-injected phase overrun beyond 900 s; halt journal event MUST carry `reason=deadline-breach-<phase> daemon-state=<live\|stopped\|handed-back>`; match against runbook step (C-10b). | Config-resolution test FAILS the build (invariant: breach→rollback must complete inside the 900s sweep window) |

---

## 6. Harness / CI Notes

**How packs run today:** bash packs are invoked directly — `bash tests/test_release_journal.sh` (2433 lines, plain self-asserting bash, no bats) and `bash tests/test_atomic_flip.sh` (365 lines); both are daemon-free, fixture-dir based. Pytest packs: `pytest tests/unit/tools/test_upgrade_journal.py` etc., `tmp_path` fixtures, real bash subprocesses with scrubbed env for lib.sh interop, real SIGKILL atomicity bounded <2s.

**New runner hook (proposed):** thin aggregator `tests/test_upgrade_overhaul_all.sh` — default fast subset (new bash packs + §5 pins, target <10min total); `ENVSIM_FULL=1` adds the full timing matrix; `ENVSIM_BASELINE=1` adds the pre/post delta drill (manual, slow). Alternative: register the new packs in the tester-gate invocation list — **decision noted for the implementer; either satisfies this strategy**.

**Expected runtimes:** new bash packs <2min each; wall-clock drill 2–4min (typical profile); full soak matrix 6–10min; pytest packs <1min each (SIGKILL test ~2s bounded); baseline drill ~30–50min (gated, manual).

**Flakiness policy:** timing asserts use median-of-N with ~100x headroom vs the 280s baseline; pid liveness uses REAL subprocesses (no /proc parsing games); no sleep-based races — bounded event-driven waits only (the `test_bounded_subprocess_waits.sh` discipline).

---

## 7. Open Items for the Implementer

1. **P-Dx resolution:** every parameterized test must be resolved to concrete assertions once `decisions.md` lands — the parameterization is scaffolding, not permission to skip.
2. **Q6a FINAL (AC2 ceiling shape)**: ≤600 s claim is **RE-SCOPED to the TYPICAL path** (4.7–5.7 min, ≤2x margin); worst-case envelope is **non-stop worst 870 s + 30 s slack inside 900 s ceiling** (all-budgets-max halt at 1400 s). NO gate-budget trimming (Oct 9 proved livez can legitimately need >300 s under load — trimming = weakening a gate). **Q6b (maiden-promote mode) CLOSED — RESOLVED to FAST by leader ruling; re-baselined risk-register §4 sign-off; conservative mode available as env-only escape hatch.** See `phase4-plan.md` D-P4.5 + `risk-register.md` §4 (RE-BASELINED).
3. **Baseline capture:** run `ENVSIM_BASELINE=1` on pre-change HEAD ONCE, early, and archive the numbers here — the AC1/AC2 evidence narrative depends on the measured before/after delta, not the digest's quoted figures alone. **Entry-gate for P2 (T2.0) MUST run before the splice lands.**
4. **C-1 soak cap-reach-with-zero-greens must exit 1, not 0** (the never-green commit bug under sustained DB churn). Pinned by `T-G2a-i` subtest of `T-G2a`; cadence is **15 s** (NOT 30 s — leader Q6a final).
5. **C-3 splice-replaces-bash-walk regression pin**: `tests/test_journal_splice.sh` T2.4a — `lock_heartbeat` does NOT invoke the bash `journal_heartbeat` walk post-patch (the splice path via `set-in-flight-field last_heartbeat` is the sole writer). Augment-not-replace keeps the slow walk on the loop.
6. **C-2 NESTED not TOP-LEVEL**: the heartbeat write goes via `set-in-flight-field last_heartbeat`; a top-level `set-field last_heartbeat` is invisible to the sweep's `in_flight.last_heartbeat` accessor. Test fixtures write `in_flight.last_heartbeat` via the splice path (C-9 fixture fix), NOT by mutating the (production-impossible) `pending_op.owner_heartbeat_at` field directly.
7. **C-12 vehicle reconcile**: `feature/checkpoint-conn-resilience` is ALREADY on `c8a3fbf6` (councilor-verified). No separate merge commit required; v0.20.1 vehicle rides the resilience as-is.
