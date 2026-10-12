# upgrade-speed-overhaul — Plan Overview

- **Mission branch**: `feature/upgrade-speed-overhaul` @ `c8a3fbf6a48d6dd1bfea97d0fb41140501a552a9`
- **Worktree (read-only w.r.t. code)**: `/home/nea/ensemble-src-wt-upg-overhaul`
- **Authoring date**: 2026-10-12
- **Author**: plan-overview worker (planning lane)
- **Status**: Ready for review — synthesizes the three authoritative inputs at this directory:
  - `decisions.md` (design decisions a–h, PR break-up, before/after timing)
  - `test-strategy.md` (AC matrix, test layers, P-Dx parameterizations, harness notes)
  - `risk-register.md` (R-01…R-14, change-rollback plan, out-of-scope, residual-risk sign-off)
- **Read order for a reviewer new to this mission**:
  1. This file (objective, scope, phasing, ACs, open questions).
  2. `decisions.md` (the design — what we are doing and why).
  3. `test-strategy.md` (how we know it works — AC matrix + layer harness).
  4. `risk-register.md` (what can still go wrong — top risks + operator runbook + sign-off).

---

## 1. Objective + Success Criteria

### Objective

Make the ensemble upgrade system **fast-by-default and reliable** so that a clean promote shrinks from **36–59 minutes today (typical; journal-dominated) to 8–13 minutes**, with worst-case envelope **14.5 minutes** (non-stop worst 870 s + 30 s slack inside the 900 s hard wall-clock ceiling; all-budgets-max ceiling 1400 s = halt-before-commit) — the budget breach routes to the existing halt machinery. The overhaul is also the **first non-shell journal writer** and the first **live ceremony to use adaptive soak** — both must ship hardened against the unchanged hard constraints (livez/readyz gates, auto-rollback, 3-factor nonce ceremony, journal txn integrity, all fail-closed checks, plugin staleness journal-and-proceed, promote-only tool-lane fence).

### Success criteria (the five quantitative ACs verbatim from test-strategy.md §1)

| AC | Verbatim criterion (test-strategy.md §1) | Pinned by |
|----|-------------------------------------------|-----------|
| **AC1** | Journal event write **< 1 s** at 100 KB / 500-entry fixture | `T-AC1a splice-latency` (`tests/test_journal_splice.sh`); pre-change baseline ~139 s/read → target median-of-5 < 1.0 s |
| **AC2** | Clean promote **≤ 10 min** typical-path drill | `T-AC2a stub-ceremony-wallclock` (`tests/test_ceremony_wallclock.sh`); typical ≈ 4.7–5.7 min; **Q6a final**: ≤600 s claim is RE-SCOPED to the TYPICAL path (≤2x margin); worst-case envelope is 870 s non-stop-worst + 30 s slack inside 900 s ceiling; all-budgets-max ceiling 1400 s halts before commit. |
| **AC3** | **Conservative mode ≈ current envelope** (bit-identical) | `T-AC3a/b/c` (`tests/test_adaptive_soak.sh`); `ENSEMBLE_PROMOTE_SOAK_S=300` + explicit budgets → exact current tuple; fail-closed matrix unchanged |
| **AC4** | **Mis-sweep impossible while executor alive** (proven) | `T-AC4a/b/c` (`tests/unit/tools/test_upgrade_journal_sweep_liveness.py`); FRESH `in_flight.last_heartbeat` (via C-2 splice path) + LIVE pid → sweep refuses to clear pending_op; defense-in-depth cells (unparseable ts, lock-held defer, fresh-heartbeat-alone) |
| **AC5** | **Rollback targets N-1 always** (proven across halt→re-promote chain) | `T-AC5a/b/c` (`tests/test_rollback_target.sh`); four-cell matrix: fresh journal · halt→re-promote (Oct 9 arc) · `boot_sweep_commit_and_continue` (cell 3 via C-4 `in_flight.displaced_version`) · double-rollback |

---

## 2. Scope + Workstream Map

### Scope (in / out — see §7 for the full out-of-scope list)

**In scope** (the six goals from the commission):

| Goal | Description | Phase | Decisions | PR |
|------|-------------|-------|-----------|-----|
| **G1** | Stall elimination: bounded retries, per-phase deadlines, total ceremony budget, fail-closed breach → existing auto-rollback/halt | P3 | (d), (e) | PR-3 |
| **G2** | Adaptive soak default 60–120 s with N consecutive greens; 300 s conservative opt-in | P3 | (c) | PR-3 |
| **G3** | Rollback correctness: N-1 always; stale-previous fix (halt→re-promote + boot_sweep) | **P1** | (g) | PR-1 |
| **G4** | Busy-daemon path: audit-after-soak, sentinel boot grace, busy-amnesty asymmetry | P3 | (e), (f) | PR-3 |
| **G5** | pending_op / orphan-sweep desync: live executor never misclassified | **P2** | (b) | PR-2 |
| **G6** | Printf noise | P1 | (h) | PR-1 (rides with G3) |

**Tier normalization (the phase-plan worker MUST apply this — re-confirmed under the architect review):**

- The decisions.md "P1/P2/P3" labels mean **WITHIN-mission ordering** — all ship in the first promote on this branch. P2/P3 are not "later." They name implementation sequencing inside a single mission window.
- The **daemon-side sweep changes in decision (b)** (three-signal read: heartbeat freshness + `os.kill` + cgroup, `journal_owner_heartbeat_fresh` accessor at `daemon/services/upgrade_journal_sweep.py:1590`) **ARE IN SCOPE** of this mission — AC4 explicitly requires the mis-sweep-impossible test to prove them; the phase plan must include them.
- **(f) audit-after-soak sequencing ships in P3** (not a later promote) — the prior decisions.md tier-mapping ("P3 = rides the next post-P1/P2 cycle") has been corrected to "P3 = rides the FIRST promote on this branch together with P1/P2" (decision narrative aligned; see `decisions.md` Tier mapping section). The build does NOT defer (f) to a later cycle.
- **C-2 + C-3 corrections** (decisions.md (b) narrative rewrite): the C-2 fix is that the heartbeat MUST write the NESTED field `in_flight.last_heartbeat` via `set-in-flight-field`, NOT a top-level `last_heartbeat` via `set-field` (the latter is invisible to the sweep's accessor). The C-3 fix is that the bash `journal_heartbeat` walk at `lib.sh:1196` MUST be REPLACED by the splice write, not augmented — augment keeps the slow walk on the 30 s loop.
- **C-8b (Q5 close)**: busy-amnesty reads the **EXISTING** `readiness.py:198/222 detail.inflight_turns` field via a readyz curl; do NOT introduce a `/run/ensemble/inflight_turns` file; do NOT introduce a daemon-side counter writer; do NOT reuse `has_instance_busy`. The plan-overview §8 Q5 closes by this ruling.

### Workstream × goal × decision × phase map

| Workstream | Goal(s) | Decision(s) | Phase | PR (suggested) |
|------------|---------|-------------|-------|----------------|
| WS-1: rollback correctness | G3 | (g) | **P1** | PR-1 |
| WS-2: printf polish | G6 | (h) smalls (lib.sh:592, promote.sh:21-23, lib.sh:120-122) | **P1** | PR-1 |
| WS-3: journal splice helper | G1 (perf), G5 | (a) | **P2** | PR-2 |
| WS-4: pending-op heartbeat writer | G5 | (b) | **P2** | PR-2 |
| WS-5: daemon sweep three-signal | G5 | (b) | **P2** | PR-2 |
| WS-6: adaptive soak | G2 | (c) | P3 | P3 |
| WS-7: total ceremony budget | G1 | (d) | P3 | P3 |
| WS-8: transient / boot handling | G4 | (e) | P3 | P3 |
| WS-9: audit-after-soak sequencing | G4 | (f) | P3 | P3 |
| WS-10: integration + vehicle staging + maiden-promote | all | (cross) | **P4** | (no PR; gate) |

---

## 3. Phase Table + Dependencies + File-Coupling Notes

### Phase table

| # | Name | File | Objective | Depends on | Suggested PR |
|---|------|------|-----------|------------|--------------|
| **P1** | Rollback correctness + adjacent polish | [`phase1-plan.md`](./phase1-plan.md) | Land N-1 invariant (decision g) + printf polish (decision h) at the smallest surgical surface. | — | PR-1 |
| **P2** | Journal splice helper + pending-op liveness | [`phase2-plan.md`](./phase2-plan.md) | Replace bash brace-walk with stdlib-only python helper; close armed→flip misclassification window via heartbeat writer + three-signal sweep. **(Entry gate: `ENVSIM_BASELINE=1` pre-change timing capture BEFORE the splice lands.)** | P1 (independent in code; the P2 entry-gate task pins the splice within the same promote) | PR-2 |
| **P3** | Adaptive ceremony: soak cap + total budget + audit-after-soak | [`phase3-plan.md`](./phase3-plan.md) | Ship the fast-by-default + reliable trio (decisions c + d + e + f) as one PR. Soak adaptive 60–120 s; 900 s wall-clock ceiling with breach→halt; classified probes + busy-amnesty extension; deferred DELETE off boot critical path. | P2 (helper exposes `set-field` for any new journal writes; sweep changes shipped in P2) | PR-3 |
| **P4** | Integration: P-Dx matrix + full pack runs + vehicle staging + maiden-promote | [`phase4-plan.md`](./phase4-plan.md) | Resolve parameterized flips; full bash/pytest/TIMING pack run; optional sandbox drill; stage v0.20.1 vehicle; prepare the maiden-promote (drill evidence archive + conservative-vs-fast decision + §7 watch runbook entry). | P1, P2, P3 | (gate, not a PR) |

### Phase dependency graph (textual, because ASCII in this template)

```
P1 (rollback + polish)
 │
 └─► P2 (splice helper + three-signal sweep)
        └─► P3 (adaptive soak + budget + transient + deferred-delete)
                    └─► P4 (integration + maiden-promote prep)
```

### File-coupling notes (load-bearing — re-verify in code before each task lands)

| File | Used by phases | Note |
|------|----------------|------|
| `scripts/upgrade/lib.sh` | P1 (P1.4, P1.5 comment refresh), P2 (P2.2 wrapper, P2.4 lock_heartbeat extension), P3 (P3.1 gate_soak refactor, P3.2 _enforce_deadline helper, P3.4 _probe_once_classified) | **SHARED** by splice, gates, and budget. The `_splice_journal` wrapper at P2 must mirror `journal_history_append` ergonomics so the 19 call sites in promote.sh change minimally. **No new external deps in lib.sh preamble.** |
| `scripts/upgrade/promote.sh` | P1 (P1.1-P1.3: commit block), P2 (P2.3: 19 migration sites), P3 (P3.1, P3.2) | The envelope: the 19 `journal_history_append` sites migrate to splice; `journal_set_previous "${OLD_CUR:-null}"` at `:438` stays the shell-coordinated commit block. `_enforce_deadline` calls inserted at every phase boundary. |
| `scripts/upgrade/rollback.sh` | P1 (P1.4: target=$OLD_CUR contract), P3 (P3.2: deadline check) | The caller passes `$OLD_CUR` as `$1`; rollback.sh reads it (not `journal.previous`) — caller-captured intent is now the source of truth. |
| `scripts/upgrade/stage.sh` | P4 (P4.4 vehicle staging) | `ENSEMBLE_BINARY_VERSION:-${VERSION#v}` at `:361`; `rollback_safe` derivation at `:325-351`; quarantine clears ONLY via re-stage at `:616-618`. **Untouched by the overhaul** — the safety gates are pinned by `T-PIN-failclosed`. |
| `scripts/upgrade/journal_splice.py` | P2 (P2.1) | **NEW FILE**. stdlib-only. Mirrors `scripts/upgrade/ledger_check.py:18-26` (no env-sniffing, explicit argv). Refuses live install via `expanduser+resolve+samefile` (ledger_check.py:46-55). Exit codes 0/1/78. Field-ownership matrix per decisions.md (a). |
| `launcher.sh` (ROOT — **NOT** scripts/upgrade/) | P1 (P1.5 boot_sweep_commit_and_continue), P2 (P2.7 comment refresh only) | **CAUTION R-08 (b): blast radius.** Boot-critical, self-contained (no lib.sh, no python3, `SWEEP_STALE_S=600 :61`). Phase P2 adds NO python3 dep to launcher. The `:1020-1037` boot_sweep_commit_and_continue becomes the **only** path that may update `previous` from boot-sweep land (operator-declared `rollback_safe:false` + `intent_flip` evidence). |
| `daemon/tools/upgrade_journal.py` | P2 (P2.5 `owner_heartbeat_at` writer surface; today ZERO writers exist for this field at `:741`) | The first-ever writer for `owner_heartbeat_at` lands in P2 via `set-field` op, hooked into the same `lock_heartbeat` extension that writes `in_flight.last_heartbeat`. |
| `daemon/services/upgrade_journal_sweep.py` | P2 (P2.5 three-signal read at `:1590`) | `(journal_owner_heartbeat_fresh) OR (os.kill(owner_pid, 0) == 0 AND cgroup_leaf matches expected systemd unit)`; `armed_at` becomes fallback only. |
| `daemon/manager.py` | P3 (P3.7 `clear_all` split) | Capture stays at `:843` + `:859-873` + `:424-426`; the DELETE moves to a lifespan-stage background asyncio task. |
| `daemon/repositories/message_queue/repository.py` | P3 (P3.7) | `clear_all` becomes a two-stage coordinator; audit log at `:1136-1143` preserved; task-table-intactness assumption at `:1144-1148` preserved. |
| `daemon/api.py` | P3 (P3.5 `BOOT_GRACE_S`, P3.6 busy-amnesty extension, P3.8 lifespan startup hook) | Busy-amnesty extension via `_read_inflight_turns_bounded` shared helper; `BUSY_INFLIGHT_BOUND=5`. |
| `tests/**` | All phases (per phase plan) | New: `tests/test_journal_splice.sh`, `tests/test_adaptive_soak.sh`, `tests/test_ceremony_wallclock.sh`, `tests/test_busy_daemon_path.sh`, `tests/test_rollback_target.sh`. New pytest: `tests/unit/tools/test_upgrade_journal_sweep_liveness.py`, `tests/unit/tools/test_upgrade_previous_semantics.py`, `tests/unit/tools/test_upgrade_splice_helper.py`. Extended: `tests/test_release_journal.sh`, `tests/test_atomic_flip.sh`, `tests/test_boot_sweep_commit_and_continue.sh`, `tests/test_bounded_subprocess_waits.sh`, `tests/test_promote_plugin_staleness.sh`, `tests/test_launcher.sh`. |

---

## 4. Timing Model — Before / After

(Verbatim from decisions.md "Final state expectations" table + E6 timing model.)

| Metric | Today (v0.18 baseline) | After this mission |
|--------|-----------------------|--------------------|
| **Clean promote wall-clock (typical)** | 28–46 min journal overhead + ~8–13 min ceremony ≈ **36–59 min** | <1 min journal + ~8–13 min ceremony ≈ **8–13 min** |
| **Clean promote wall-clock (worst-case observed)** | 3 h 15 min (3h15m failure arc) | ~16 min (worst-case envelope: 900 s ceremony + <1 s journal) |
| **Soak duration (default)** | 300 s (flat wait) | **60–120 s adaptive**; early exit on 3 consecutive greens @ 15 s cadence; floor 60 s; cap 120 s |
| **Boot-storm-induced false-red probability** | ~10% (1.5 s transient window race) | **<0.1%** (60 s grace + bounded retries + 3-consecutive-green requirement) |
| **Halt→re-promote-chain rollback distance** | N-2 sometimes (when N-1 halted after flip before commit — Oct 9 arc) | **Always N-1** (immediate previous from `$OLD_CUR` captured at `:227 :436`, immediacy-asserted at `:438`) |
| **Pending-op misclassification window (armed→flip)** | ~37 min (slow bash walk — `journal_heartbeat` does ~280 s/call vs 30 s nominal; sweep's `PendingOp.owner_heartbeat_at` reader points at a field with zero writers) | **<2 min** (splice REPLACES bash walk at `lib.sh:1196` per C-3; cadence now matches `LOCK_HEARTBEAT_S=30`; sweep's `in_flight.last_heartbeat` reader sees the stamp — C-2 + C-3 corrections) |

### Phase budgets table (single source of truth — from decisions.md (d))

| Phase | Budget (s) | Cite (today) | Notes |
|-------|-----------|--------------|-------|
| Preflight + integrity | 45 | (no current budget; measured ~35 s) | tightens pre-rollback window |
| Stop ensemble | 240 | `_derive_parent_stop_budget` at `lib.sh:2756` (default eff 225 s, worst 755 s) | **USE TODAY'S DERIVED BUDGET, not a new constant** — single source |
| Atomic flip | 30 | unwritten today (1 s typical) | generous for slow disk |
| Restart / handback | 90 | `HANDBACK_START_BUDGET_S=60` at `lib.sh:95` + 30 s slack | tight; needs assertion via `_run_bounded` |
| Livez | 180 | `LIVEZ_BUDGET_S=180` at `lib.sh:78` | preserve |
| Readyz | 120 | `READYZ_BUDGET_S=120` at `lib.sh:79` | preserve |
| Version | 30 | (unwritten; 5 s typical) | generous |
| Adaptive soak | 60–120 (adaptive per (c) at **15 s cadence**) | (c) — C-1 cap semantics: cap-reach-without-3-greens → exit 1 → auto-rollback | cap shrinks against remaining budget |
| Commit | 30 | (unwritten; measured 2–4 s today) | generous |
| **TOTAL ceiling** | **900** | (NO analog today) | **NEW — top-level cap; halt on breach (C-10c reason token `deadline-breach-<phase>`; C-10b halt-detail `daemon-state` field)** |

**C-10a reconciled arithmetic (single number with its arithmetic shown):**

- **Typical**: ≈ 282 s = 4.7 min (per observed non-journal ≈ 221 s + post-splice journal < 1 s + soak default 60 s).
- **Non-stop worst**: 870 s = 14.5 min (stop default eff 225 + every other budget at its ceiling + adaptive soak 120 s) → **30 s slack inside the 900 s ceiling**.
- **All-budgets-max halt ceiling**: 1400 s (stop worst 755 + every budget max + adaptive soak squeeze at the boundary yields 105 s of soak, not 120 s) — breached → halt before commit.

**Adaptive soak coupling (d; Q6a final):** `gate_soak` receives `ceremony_remaining_s` as a new arg (or reads a `promote_deadline` env var); adaptive cap is `min(ADAPTIVE_SOAK_CAP_S, ceremony_remaining_s - 60)`. If `ceremony_remaining_s < 60`, soak runs with `0s` and journals the deferral. ≤600 s claim is RE-SCOPED to the TYPICAL path; worst-case envelope is 870 s + 30 s slack inside 900 s.

### Test-strategy.md arithmetic evidence (test-strategy.md §1 row T-AC2c)

Today: 19 append sites × ~280 s/event ≈ **28–46 min journal-dominated** at 87 KB (139 s/read × ≥2 reads per update, ×N events). Post-splice: ≤ 19 s total journal cost. E6 non-journal budgets 536 s; typical observed non-journal ≈ 221 s. With adaptive soak 60–120 s: **typical 4.7–5.7 min (≤ 10 min with ~2x margin)**; **non-stop worst 870 s + 30 s slack inside 900 s ceiling**; all-budgets-max ceiling 1400 s (halt). E6 clean design budget 8–13 min at 300 s soak reconciles (536 + 300 ≈ 836 s ≈ 13 min).

---

## 5. Risk Summary

(Detailed table at `risk-register.md` §1; this is the top-5 + a pointer.)

| ID | Risk | Likelihood | Impact | Mitigation | Detection |
|----|------|-----------|--------|------------|-----------|
| **R-01** | **Maiden voyage / self-hosting bootstrap.** The first execution of the fast path is a real production promote. Latent fast-path bugs fire on the vehicle carrying them. | High (certain) | High | Conservative-mode opt-in (`ENSEMBLE_SOAK_CONSERVATIVE=1`) is the maiden-promote default; auto-rollback untouched + proven; sandbox drill before ceremony; §7 watch runbook entry filed per risk-register §2. | `T-AC2a` drill evidence archived; ceremony journal live; post-promote watch window (livez/readyz + pending_op state + rollback-pointer currency). |
| **R-02** | **Splice helper = first non-shell journal writer.** Torn-write / lock / format semantics must be identical to shell, or journal integrity silently forks. | Medium | High | Schema-freeze (same fields, only faster); real-SIGKILL atomicity test mirrored from `test_upgrade_journal.py:218-251`; shell↔python lock interop both directions; byte-identity of untouched regions asserted on every splice. | `T-AC1b`, `T-NEG-splice-torn`, `T-NEG-lock-contention`, `tests/test_journal_splice.sh`, `tests/unit/tools/test_upgrade_splice_helper.py`. |
| **R-03** | **python3-from-PATH variance on upgrade hosts.** Host `python3` may be absent / ancient / venv-less; repo floor is 3.13 but HOST python is what `ledger_check.py`-style helpers resolve. Hosts without usable python3 turn fast path into fail-closed exit-78. | Medium-High | High | Helper is stdlib-only (ledger_check.py precedent); explicit version-floor assert at startup; preflight probes python3 and journals the result BEFORE any destructive step; refusal is byte-safe; **P-Dx**: shell-fallback vs fail-closed — tests cover BOTH options so the decision can flip without test rework. | `T-NEG-python3-missing`; preflight-probe journal event test; fleet note in operator runbook. |
| **R-04** | **Adaptive soak too short → regression ships.** 60–120 s may miss slow-burn regressions the 300 s conservative soak caught (memory balloon, slow request decay, connection-pool exhaustion). | Medium | High | N-consecutive-green required (not a single lucky probe); version-drift mid-soak still fails (`:4258` pin); 900 s hard cap unchanged; conservative 300 s opt-in preserved and documented as the maiden-promote recommendation; post-promote §7 watch. | `T-G2a/b` choreography tests; live post-promote monitoring. |
| **R-10** | **Total-budget enforcement deadlock/livelock** interacting with the pipeline lock, launcher `SWEEP_STALE_S=600` presumed-dead window (`:61 :908-915`), and sweep `EXECUTOR_STALENESS_WINDOW_S=900` (`:82`). Hazards: budget consumed while blocked on lock; a ~600 s ceremony coinciding exactly with launcher's 600 s window; budget expiry racing the sweep. | Medium | High | Pinned invariant `total_budget < 900 s` so breach→rollback completes inside the sweep's stale window; lock-wait accounting made explicit (**P-D1**: counts-in vs counts-out, both tested); respect the 600 s coincidence — keep effective total ≤ 540 s OR verify lock-held defer (launcher `:913-936`) + boot-sweep evidence check (`:491`) cover an in-flight promote (tested interop). | `T-G1c` invariants + livelock probe; launcher-sweep interop tests (`T-AC4b-ii`). |

**Full table**: see `risk-register.md` §1 for R-01 through R-14.

---

## 6. Release Vehicle — v0.20.1

**Vehicle: v0.20.1 — riding the NEXT promote.** The first fast promote is the maiden voyage of the overhaul.

### stage.sh mechanics that govern v0.20.1

| Mechanic | Anchor | Stance for v0.20.1 |
|----------|--------|--------------------|
| **Binary version derivation** | `stage.sh:361`: `ENSEMBLE_BINARY_VERSION:-${VERSION#v}` | Set `ENSEMBLE_BINARY_VERSION` explicitly if the commission-distinct release name does NOT start with `v`. **Mandatory for v0.20.1 — `VERSION="v0.20.1"` resolves cleanly, no override needed.** |
| **Version-verify fail-closed** | `stage.sh:353-360` vs `daemon/__init__.py __version__` | Exit-78 on mismatch. Pinned by `T-PIN-failclosed`. **Untouched by the overhaul.** |
| **`rollback_safe` derivation** | `stage.sh:325-351`: unset + destructive-DDL ⇒ exit-78 | **Untouched by the overhaul** — the overhaul never flips this gate. |
| **Quarantine clears ONLY via re-stage** | `stage.sh:616-618` | **Fail-closed by design**, pinned by `T-PIN-failclosed`. A bad/stuck stage leaves the target quarantined; the runbook (§2) must call this out. |
| **Re-staging the SAME version** | vehicle naming interaction with `:361` | Either bump the version or set `ENSEMBLE_BINARY_VERSION` explicitly, or the stage may pick up a stale binary under the same name. **Runbook entry required.** |

### ENSEMBLE_BINARY_VERSION naming implication

- `stage.sh:361` derives `ENSEMBLE_BINARY_VERSION:-${VERSION#v}`. The release name is `v0.20.1` → `ENSEMBLE_BINARY_VERSION=0.20.1` resolves cleanly.
- If the commission picks a non-`v`-prefixed name, an explicit `ENSEMBLE_BINARY_VERSION` is required or `version-verify` fails (test-strategy §3 T-PIN-failclosed).

### Maiden-promote mode decision (R-01, R-04) — RESOLVED

- **Default recommendation for the maiden promote**: **FAST mode** (default-mode behavior — adaptive 60–120 s soak @ 15 s cadence, 3 consec greens, 900 s ceiling). The risk-register §4 sign-off is **RE-BASELINED to FAST** (per leader ruling Q6b final; conservative mode available as env-only escape hatch `ENSEMBLE_SOAK_CONSERVATIVE=1`). Q6b closes — no more caller decision.
- The project's standing "PREFER SPEED OVER MAXIMAL SAFETY" promote directive (project metadata, effective from next-upgrade-after-2026-10-01 v0.16.8 promote) **aligns** with this default.
- **Compensating controls** (per `phase4-plan.md` D-P4.6 + `risk-register.md` §2): the §7 watch runbook entry MUST include ALL FOUR C-11 expansions (post-promote real-message smoke mandatory; first destructive blob-prune drain watch; no-key-rotation window; transient indicator list) + the C-10b post-stop breach first-recovery-step.

---

## 7. Out-of-Scope List

(Verbatim from risk-register.md §3. Anything not in this list is IN scope; if a checklist item says OUT, do not touch it without surfacing an open question.)

| Item | Stance |
|------|--------|
| **GNU-date debt**: `retention_evict` `stat -f '%m'` (`lib.sh:4313`); `_js_count_rollback` `date -jur` (`launcher.sh:466`) | OUT — unless incidentally touched, then R-11 protocol (portable swap + BSD-branch test). |
| **Pool `check()` event-loop hardening** | **EXPLICIT DEFER.** Rationale: unrelated to ceremony speed/reliability; folding an unrelated runtime change into the same vehicle as the fast path maximizes maiden-voyage delta (R-01) for zero mission benefit. Record as a standalone micro-commission candidate. |
| **PVC alerting** | Deferred (unchanged from commission). |
| **Promote-only tool-lane fence** | Untouchable — protected by characterization pins (`T-PIN-fence`); any proposal to touch it is out of scope by construction. |
| **Retention / vacuum subsystem** | Untouched — v0.20.0 just shipped it (blob-prune default-on); its §7 watch ownership lives elsewhere and must not couple to this mission. |

---

## 8. Open Questions for the Caller

These are consolidated from decisions.md Q1–Q5, test-strategy.md §7, AND the residual tension surfaced in risk-register.md §4. **Surface them; do NOT silently absorb.**

| # | Source | Question | Caller |
|---|--------|----------|--------|
| **Q1** | decisions.md | Does the operator want a separate `ENSEMBLE_PROMOTE_DEADLINE_S` override for the total ceremony budget, or is `PROMOTE_TOTAL_DEADLINE_S=900` enough? The digests say not — but the project's standing "PREFER SPEED" directive suggests overrides are not needed in default config. | planner → operator |
| **Q2** | decisions.md | Should `BUSY_INFLIGHT_BOUND` be 5 (default) or a per-instance tunable? `=5` is a static guess; production might want it observable. | planner |
| **Q3** | decisions.md | For (f)'s deferred DELETE, should the background task be asyncio-managed from `lifespan startup` (current proposal), or a separate `MessageQueueJanitor` service? Single-process is simpler but couples pool lifetime to DELETE lifetime. | planner → architect |
| **Q4** | decisions.md | **CLOSED by C-4.** `in_flight.displaced_version` is captured at `journal_open_txn` (`lib.sh:700-712` extend); boot_sweep reads it. Test pin is `T-AC5a cell 3` per `tests/test_rollback_target.sh` (extended) and `tests/test_boot_sweep_commit_and_continue.sh` (extended). Old-format txn → halt-for-human (consistent with "halt-for-human is BY DESIGN"). | n/a | n/a |
| **Q5** | decisions.md | **CLOSED by C-8b.** Busy-amnesty reads the **EXISTING** `readiness.py:198/222 detail.inflight_turns` field via a readyz curl (no new `/run/ensemble/inflight_turns` file; no new daemon-side counter writer). The decision is `decision (e) busy-amnesty` per decisions.md (e) Concrete Contract. | n/a | n/a |
| **Q6** | test-strategy.md §7.2 + risk-register.md §4 | **Q6a FINAL**: ≤600 s claim is **RE-SCOPED to the TYPICAL path** (4.7–5.7 min, ≤2x margin). Worst-case envelope is **870 s non-stop-worst + 30 s slack inside 900 s ceiling**; all-budgets-max halt ceiling 1400 s. No gate-budget trimming (Oct 9 proved livez can legitimately need >300 s under load). **Q6b CLOSED** (leader ruling): **maiden-promote mode is FAST** (default adaptive mode); risk-register §4 sign-off RE-BASELINED to FAST; conservative mode available as env-only escape hatch `ENSEMBLE_SOAK_CONSERVATIVE=1`. The C-11 runbook controls (post-promote real-message smoke; first destructive blob-prune drain watch; no-key-rotation window; transient indicator list) attach as compensating controls per `phase4-plan.md` D-P4.6 / T4.6. | n/a (resolved) | n/a |
| **Q7** | test-strategy.md §7.1 | **P-Dx resolution discipline:** every parameterized test must resolve to a concrete assertion before merge — the parameterization is scaffolding, not permission to skip. Caller confirms the implementer will resolve the matrix when P3 lands (not silently pick one). | planner → tester |
| **Q8** | test-strategy.md §7.3 | **Baseline capture (`ENVSIM_BASELINE=1`):** runs on pre-change HEAD ONCE, early, and archives numbers here. **The AC1/AC2 evidence narrative depends on the measured before/after delta, not the digest's quoted figures alone.** **This is a Phase-2 entry-gate task (P2.0)** — the splice MUST NOT land before the baseline is captured and archived. | planner → implementer |

---

## References (link out to authoritative sources for depth)

- **Decisions** — `decisions.md` §(a)–(h), PR break-up, before/after table.
- **Tests** — `test-strategy.md` §1 AC matrix, §2 goal map, §3 hard-constraint pins, §4 test layers, §5 negative tests, §6 harness, §7 open items.
- **Risks** — `risk-register.md` §1 risk table, §2 change-rollback plan, §3 out-of-scope, §4 residual-risk sign-off.
- **Hard-cap anchors verified by re-read** (current at HEAD `c8a3fbf6`):
  - `promote.sh:156` (soak cap 900); `:177 :301 :342 :427 :503 :2526 :2532 :4249` (lock_heartbeat call sites); `:438` (set_previous in commit block); `:452-453` (PREV_JSON + PREV read — replaced by `$OLD_CUR` in P1).
  - `lib.sh:78` (`LIVEZ_BUDGET_S=180`); `:79` (`READYZ_BUDGET_S=120`); `:95` (`HANDBACK_START_BUDGET_S=60`); `:108 :113` (`LOCK_HEARTBEAT_S=30` / rwlock separation); `:537-545` (`journal_path`); `:546-584` (`journal_read`); `:609-666` (`journal_update`); `:676-687` (`journal_history_append`); `:1186-1197` (`lock_heartbeat`); `:4240-4266` (`gate_soak`); `:4258` (mid-soak version drift fails soak).
  - `daemon/tools/upgrade_journal.py:741` (`owner_heartbeat_at` field — zero writers today); `:737` (`armed_at`).
  - `daemon/services/upgrade_journal_sweep.py:82` (`EXECUTOR_STALENESS_WINDOW_S=900`); `:1590` (the read site for `owner_heartbeat_at or armed_at` — three-signal refactor in P3).
  - `daemon/manager.py:843 :859-873 :424-426` (boot ordering); `daemon/repositories/message_queue/repository.py:1136-1143 :1144-1148` (audit log + task-table assumption); `daemon/api.py:415 :812 :2873-2895` (Stop-frozen heartbeat amnesty + busy-set witnesses via `has_instance_busy`).
  - `stage.sh:325-351` (`rollback_safe` derivation); `:353-360` (version-verify); `:361` (`ENSEMBLE_BINARY_VERSION` derivation); `:616-618` (quarantine clears ONLY via re-stage).
  - `launcher.sh:59-60 :745-746` (self-contained — no lib.sh, no python3); `:61` (`SWEEP_STALE_S=600`); `:466` (`_js_count_rollback` date -jur — OUT of scope); `:908-915 :925-936` (D5 defer + under-lock revalidation); `:1009-1019` (boot_sweep comment — refreshed in P1 + P2); `:1020-1037` (boot_sweep_commit_and_continue — patched in P1).
  - `scripts/upgrade/ledger_check.py:18-26 :46-55` (stdlib-only python helper; live-path refusal pattern; exit code 0/1/78/2) — the template for `journal_splice.py`.

---

**End of plan-overview.md**