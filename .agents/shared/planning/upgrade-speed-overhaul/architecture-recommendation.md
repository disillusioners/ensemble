# Architecture Recommendation — upgrade-speed-overhaul (architectural depth enrichment)

- **Date:** 2026-10-12
- **Mission:** upgrade-system overhaul — fast-by-default + reliable promote ceremonies (`feature/upgrade-speed-overhaul` @ `c8a3fbf6`, worktree `/home/nea/ensemble-src-wt-upg-overhaul`)
- **Method:** 3 parallel analyst workers (data-flow-design on the splice boundary; resilience-design ×2 on liveness/budget and rollback/release), each verifying the plan's working defaults against the actual code. All findings below carry worker-verified file:line evidence.
- **Leader rulings in force (not re-litigated):** soak default = 60s adaptive w/ 3 consecutive greens (conservative opt-in bit-identical); maiden v0.20.1 promote runs FAST mode (env-only escape hatch; auto-rollback untouched). **Both rulings VALIDATED** — no hard evidence to challenge either; the corrections below make the rulings' semantics coherent rather than reversing them.
- **Inputs:** plan-overview.md, decisions.md §(a)–(h), phase1-4-plan.md, test-strategy.md, risk-register.md, incident-20261010-checkpoint-connection-pool.md, and the cited code (lib.sh, promote.sh, rollback.sh, stage.sh, launcher.sh, upgrade_journal.py, upgrade_journal_sweep.py, readiness.py, api.py, manager.py, message_queue/repository.py).

---

## 1. Verdict

**The plan is architecturally sound at the macro level** — phase sequencing, fail-closed direction, halt-machinery reuse, field-ownership matrix, launcher purity, and rolling back-compat all verify against code. **Four working defaults contain real defects that must be corrected before implementation** (C-1…C-4 below; three are 🔴), plus eight smaller corrections. None reverses a leader ruling; all are implementable within the existing phase/PR structure.

---

## 2. Focus-Area Rulings

### 2.1 Journal splice protocol boundary (Phase 2 / decision (a); risks R-02/R-03/R-09)

**VALIDATED with two corrections (C-2 grammar gap, C-6 invariant reframe) and full protocol spec (§3).**

- **Lock contract — caller-owns-lock is CORRECT.** All 19 `journal_history_append` sites + the commit block run under `lock_acquire` (promote.sh:165). The helper never probes the pipeline lock. The orphan-helper case (caller SIGKILLed mid-helper-write) is safe: the caller is the decision-maker; if it died, no decision depends on the in-flight write's pre-state; `os.replace` is atomic either way.
- **Atomicity — no fsync, matching today.** `lib.sh:587-603 journal_write` does not fsync; the D4 spec is "atomic mv," not durable-on-power-loss. The daemon lane's own writer fsyncs (`upgrade_journal.py:298`) — the asymmetry is by design (different lanes, not on each other's critical path). SIGKILL between tmp-write and replace leaves an orphan `state.json.tmp.<pid>…` — **no reader or sweeper globs the journal dir** (verified across launcher `_js_journal_read`, sweep, rollup) — harmless; best-effort `unlink(missing_ok=True)` on the OSError path.
- **Anchored readers — all compatible with compact JSON.** Every consumer verified: `_json_field` (lib.sh:239-291), `_json_field_quoted` (:305-320), `_json_sub` (:332-362), `_json_has` (:323-328), launcher `_js_json_field`/`_js_json_sub`/`_js_journal_update`/`_js_history_append` (launcher.sh:271-445), `upgrade_journal.py:247`, sweep `:1591`, `ledger_check.py:175`. Today's file already has mixed whitespace (lib.sh:656 no-space vs launcher.sh:421 space) and all anchored readers trim — compact `json.dumps` output is safe.
- **Launcher purity — claim HOLDS.** launcher.sh never sources lib.sh, has zero python3 (grep-verified), and must keep using its own `_js_*` family for `boot_sweep_commit_and_continue` (launcher.sh:1027-1037). The helper is promote-lane only. Documented fork boundary: correct.
- **Rolling back-compat (R-09) — HOLDS both directions.** Old N-1 readers (brace-scan + anchored extractors) consume compact JSON; new readers (`json.loads`) consume any well-formed JSON. Schema-freeze (speed-only, additive fields) verified.
- 🔴 **Correction C-2:** the spec'd heartbeat op is broken — see §2.2.
- 🟡 **Correction C-6:** R-02's "byte-identity of untouched regions" is **unachievable** — `json.load → json.dump` rewrites every byte. The correct, implementable invariant: **key order preserved from input (Python 3.7+ dict order) + non-target field values byte-identical after round-trip**, under the pinned serialization rule in §3.

### 2.2 Sweep liveness three-signal (Phase 2 / decision (b))

**VALIDATED with the trust table made explicit (below), one grammar correction (C-2), and one implementation correction (C-3).**

**Trust hierarchy / truth table (leave pending_op ALONE when ANY holds):**

| # | Condition | Rationale |
|---|-----------|-----------|
| 0 | `in_flight` txn open | **ABSOLUTE gate** — no exceptions mid-ceremony (uj:1618-20; sweep:1523-27) |
| 1 | pid alive ∧ cgroup-leaf match ∧ heartbeat fresh ≤60s | full corroboration |
| 2 | pid alive ∧ cgroup match ∧ evidence unparseable | existing fail-closed read (sweep:1592-95) |
| 3 | pid alive ∧ cgroup indeterminable (non-systemd) ∧ heartbeat fresh ≤60s | degrade to 2-signal |
| 4 | pid alive ∧ cgroup match ∧ evidence age ≤900s | wedged-shell tolerance window |

**CLEAR-eligible** (reconcile's own gates still apply): pid ESRCH-dead — *even if heartbeat fresh* (a heartbeat cannot resurrect a dead pid; hb-fresh-alone blocking costs only ≤60s extra janitor pin — **ACCEPT** the plan's conservative direction: pending_op is a janitor target, not a gate); pid alive ∧ cgroup mismatch ∧ hb stale >60s (recycled pid — `os.kill` false-alive, F4); pid alive ∧ cgroup match ∧ evidence age >900s.

- **Ceiling** = `EXECUTOR_STALENESS_WINDOW_S = 900` on the guard; true worst-case pipeline pin = max(900, armed+600+grace) ≈ 1200s orphan lifetime; mid-ceremony pin is absolute but bounded by the ceremony's own ≤900s budget. **Terminal action on ceiling** = the EXISTING reconcile close paths (terminal-event close or expiry close, each writing a `sweep` journal event, uj:1632-47/1665-82) — add evidence-age numbers to the detail; nothing new needed.
- **Heartbeat cost, numerically:** bash `journal_heartbeat` (lib.sh:794-822) ≈ read-walk + update-walk ≈ **~280s/call vs a 30s cadence — >900% duty, impossible**; splice <1s → **~3% duty**. Decision (b) is strictly gated on (a) — both in PR-2, correct.
- 🔴 **Correction C-2 (the sharpest P2 finding):** `phase2-plan.md:37` / decisions.md (a)+(b) spec `_splice_journal set-field last_heartbeat …` — but `set-field` writes a **TOP-LEVEL** key while the sweep reads **nested `in_flight.last_heartbeat`** (sweep:1590). As spec'd, the heartbeat is invisible to its only reader and the entire purpose of (b) silently fails. **Fix: add op `set-in-flight-field <key> <raw-json-literal>`** (creates `in_flight={}` if absent; refuse if non-object). Nested `journal_mark_*` functions (`:714 :733 :769-823`) stay bash (whole-object writers) — consistent with the field-ownership matrix, just under-documented; fence the future `open-txn` op explicitly OUT of P2 scope (arch §3 wins).
- 🔴 **Correction C-3:** T2.4 must **REPLACE** the bash `journal_heartbeat` call at lib.sh:1196, not augment it — otherwise the 139s walk stays on the 30s hot loop and (b)'s perf premise is void.
- **executor_exit idempotency:** current idempotent-when-already-nulled behavior (`reconcile_pending_op` with op=None → silent None, uj:1613-15) is **CORRECT** — state converges; the audit trail retains the mis-sweep `sweep` event + `executor_exit` (reaper journals independently, sweep:1776). 🟢 Optional WARN canary when a terminal event lands with op already None.
- **Sub-600s ceremony vs sweep timing (R-10):** a live fast ceremony keeps `in_flight.last_heartbeat` fresh (30s cadence) → launcher hb_stale=0 → never reclaimed even >600s (launcher:895-904, :922-23 lock-held defer, :928-40 revalidation); reconcile's in_flight-open gate is the second absolute layer. **Budget must count lock-wait (counts-in)** — wall-clock is the hard constraint. The 900-total vs 900-staleness race at breach is benign (breach → exit → owner dead → hb stale → launcher reclaim is the CORRECT post-mortem). **R-10's ≤540s pin may be RELAXED** with this proof recorded in decisions.md.

### 2.3 Adaptive ceremony + budget model (Phase 3 / decisions (c)+(d)+(e)+(f))

**VALIDATED with one 🔴 semantic correction (C-1), an arithmetic reframe (C-10a), and two implementation rulings (C-8).**

- 🔴 **Correction C-1 — the cap rule as written commits a never-green soak.** decisions.md:206 / D-P3.1: "If `now >= adaptive_cap` (no early exit) → exit 0 SUCCESS." Under sustained transient churn (exactly the recurring DB-churn class of incident 2026-10-10 — and certain once `feature/checkpoint-conn-resilience`'s checkpoint_saver 503 probe merges), the gate would reach cap with **zero greens ever seen and still exit 0 → commit**. Today the same churn → instant red → auto-rollback. **Correct rule: at cap, `consecutive_green ≥ 3` → exit 0 SUCCESS, ELSE exit 1 → auto-rollback.** With the fix: sustained churn terminates at cap (≤120s later than today), same terminal state, **no new wedged state**. Also delete the incoherent "floor+cap" clause (floor 60 + cap 120 = 180s is never a boundary inside a 120s-cap envelope).
- **Timer placement + arithmetic (C-10a):** `promote_deadline` computed top-level in promote.sh after lock+txn-open; `_enforce_deadline` in lib.sh at every phase boundary — VALIDATED. Corrected worst-case arithmetic: nominal phases 45+30+90+180+120+30+120+30 = 645s + stop eff-225 (lib.sh:2756: 120+WAIT_S=70+10+5+20) = **870s worst ≈ 30s slack** (not 15s — the dynamic soak squeeze `min(120, remaining−60)` at the 735s boundary yields 105s). Stop worst-case 755s (WAIT_S=600) → halt: **intended trade** per G1, but see C-10b.
- **Breach routing — VALIDATED into EXISTING machinery:** close_txn + halt journal event + lock_release + exit 78 via the promote:459-496 channel; NOT auto-rollback (daemon live+clean), NOT a new path. Halt arms **no cooldown** (cooldown is rollback-owned anti-flap; halt-retry is fail-closed per-attempt — journal-growth only). 🟢 Use a distinct reason token `deadline-breach-<phase>`.
- 🟡 **C-10b — post-stop breach leaves the daemon DOWN:** a deadline halt after stop-ensemble but pre-flip strands a stopped daemon until human action. Halt detail must state daemon-state; runbook first-recovery-step = verify/restart current, then re-promote.
- **Transient classification — table VALIDATED, implementation corrected (C-8a):** curl exit 7/28 + HTTP 503 = transient; other 5xx/4xx = red; 3 retries 1s/2s/4s (≤~27s per site, inside budgets). **BUT** `curl -fsS` suppresses the body on exit 22 — classify via `curl -sS -o <tmp> -w '%{http_code}'`, never body-sniffing after `-f`.
- **Busy-amnesty (Q5 RESOLVED, C-8b):** read the **EXISTING readyz `detail.inflight_turns`** (readiness.py:198/222 — the endpoint variant is free). Do NOT build the `/run/ensemble/inflight_turns` file (race-prone, new surface); do NOT reuse `has_instance_busy` (different predicate: PENDING+RUNNING+PAUSED tasks vs readiness turns). Bound ≤5, every amnesty journaled, bounded by phase budget + soak cap + 900 → R-05 satisfied. 🟡 Bind grace wording explicitly: grace NEVER exceeds the enclosing phase budget + global 900.
- **Audit-after-soak — VALIDATED including the two-boots case:** in-process asyncio task dies with the process; boot-2 re-derives doomed ids idempotently (DELETE-by-id no-op on missing rows); epoch-before-wipe order (manager:859-873) holds; pre-epoch rows excluded from queue_freshness so readyz-green-with-pending-backlog is sound. 5-min bounded task + lifespan wiring appropriate. Crash between soak-pass and deferred DELETE → next boot cleans (R-06 confirmed).
- **Incident interplay:** the 60s soak loses NOTHING against the readyz-green/checkpoint-dead class — 300s is equally blind (the probe uses a fresh connection). **Compensating control (mandatory, not optional): post-promote smoke = ONE real instance message end-to-end** in the maiden runbook §7 (C-11).

### 2.4 Rollback correctness (Phase 1 / decision (g))

**Read-site fix VALIDATED; boot_sweep previous-write source is WRONG and must be corrected (C-4).**

- **Read-site fix:** replacing the `journal.previous` read at promote.sh:452-453 with caller-captured `$OLD_CUR` (bound :227/:436) eliminates the N-2 bug class for the live promote chain. All six pointer-algebra chains (fresh success; halt-after-flip→auto-rollback→re-promote [Oct 9 arc]; halt→manual rollback.sh→re-promote; boot_sweep path; double rollback; adopt_stale_txn) **walk clean under the FORWARD semantic** ("previous = the version displaced by the most recent terminal event"). The immediacy assertion at commit (journal.current == $VERSION else halt 78) is sound defense-in-depth against a launcher-vs-promote TOCTOU. `_json_field_quoted` precedent already present at HEAD (lib.sh:1355-1364) — no ordering hazard. R-07 avoided: read-time normalize, no blanket refuse; AC5 cell 2 completes with no human-halt.
- 🔴 **Correction C-4 — D-P1.4 captures the wrong field:** boot_sweep_commit_and_continue spec'd `OLD_CUR=$(journal.previous)` — under FORWARD semantics that is **N-2** (predecessor-of-predecessor), not the pre-flip current (N-1). A subsequent manual rollback-without-`--to` targets the wrong release. The plan's rejected alternative C was right. **Recommended shape (keeps P1 pure bash, no helper dependency):** capture the displaced version at `journal_open_txn` time (lib.sh:700 already writes the whole `in_flight` object in bash — extend it to stamp `in_flight.displaced_version` = journal.current at open); boot_sweep reads `in_flight.displaced_version` and sets previous=that. Old-format txns (opened pre-change, swept post-change — a one-window edge on the maiden vehicle): treat as **halt-for-human** (ambiguous state), consistent with the "halt-for-human is BY DESIGN" recovery invariant.
- 🟡 **ADR needed — `previous` semantic:** all three writer paths are consistently FORWARD today (promote commit writes $CUR-displaced; auto-rollback writes $CUR (degenerate, accepted per :549-551 comment); rollback.sh:212 writes $OLD_CUR = the version rolled back FROM). The operator mental model is BACKWARD ("where I'd roll back to"). FORWARD is the implemented reality and the pointer algebra is clean under it — **document FORWARD in an ADR** + a status-display note so operators aren't misled. No code change.
- **GNU debt isolation — CONFIRMED:** launcher.sh:1009-1037 (P1 touch) is 543 lines from launcher.sh:466 (`date -jur`) with no shared function; lib.sh:4313 untouched by plan scope. R-11 mitigation stands.

### 2.5 Release vehicle v0.20.1 (Phase 4)

**VALIDATED with a reconciliation requirement (C-12) and runbook contents enumerated (C-11).**

- **Staging:** `VERSION="v0.20.1"` resolves via stage.sh:361 → `0.20.1`; version-verify must match `daemon/__init__.py __version__` (stage.sh:353-360). **Re-stage of the SAME version requires an explicit `ENSEMBLE_BINARY_VERSION` OR a version bump** — stage picks up whatever is in $REPO_ROOT; risk-register §2 item 2 already names it; runbook must call it out. Quarantine clears ONLY via re-stage (stage.sh:616-618; T-PIN-failclosed).
- **Maiden FAST-promote runbook §7 must contain:** (a) conservative escape hatch `ENSEMBLE_PROMOTE_SOAK_S=300` / `ENSEMBLE_SOAK_CONSERVATIVE=1` (env-only, T-AC3-pinned); (b) version-drift + boot-storm watch with the transient indicator list (curl 7/22/28, 503s, 3×retry backoff — so the operator doesn't intervene prematurely); (c) **first destructive blob-prune drain watch** (24h cadence on v0.20.0-class release NOT yet run — may land during the maiden window); (d) **post-promote smoke = one real instance message end-to-end** (2026-10-10 proved readyz-green can hide a dead checkpoint layer); (e) 3-factor nonce ceremony steps; (f) no-live-promote-overnight boundary; (g) "no key rotation during the ceremony window" (env-ref bridge does not propagate to OD daemon's physical key copy).
- **Sandbox drill scope (validated):** CAN validate — ENSEMBLE_SELF_ENV=sandbox fencing, mktemp INSTALL_DIR, ledger_check-style live-path refusal, stub-ceremony wallclock (T-AC2a), executor cgroup survivorship via `systemd-run --user` transient unit, adopt_stale_txn drills; abort criterion = any real unit name appearing in `systemctl list-units`. CANNOT validate (watch items, not blockers) — real handback variance, production boot storms (R-14), first REAL fast promote on production-scale journal + busy daemon simultaneously (R-01), slow-burn regressions a 60-120s soak structurally cannot catch (R-04).

---

## 3. Protocol Spec — `scripts/upgrade/journal_splice.py` (implementable, no re-derivation)

**Invocation:** `python3 scripts/upgrade/journal_splice.py <journal_path> <op> [op-args…]`; lib.sh wrapper `_splice_journal <op> [args…]` resolves `$JOURNAL_PATH`, passes `"$@"` (no shell eval), returns the helper's exit code. One fork per call — acceptable at 19 sites over a 5-10min ceremony. Free-text detail via argv is safe (no shell between wrapper and helper; ARG_MAX ≫ 2KB/site).

**Op grammar (corrected):**

| Op | Argv | Mutates |
|----|------|---------|
| `append-history` | `<event-kind> <detail-string>` | top-level `history` append |
| `set-field` | `<top-level-field> <raw-json-literal>` | ONE top-level key |
| **`set-in-flight-field`** (NEW — C-2) | `<nested-key> <raw-json-literal>` | `in_flight[nested-key]`; create `{}` if absent; refuse non-object |
| `unset-field` | `<top-level-field>` | → null |
| `clear-in-flight` | — | `in_flight` → null |
| `set-previous` | `<quoted-version \| "null">` | `previous` only |

**Never in helper:** `set-current` (bash `journal_set_current`, lib.sh:826), pending_op family (python reconciler owns), counters/cooldown/quarantine/rollback_window_count (bash owners), `open-txn` (bash `journal_open_txn` :700 — explicitly fenced out of P2).

**Exit codes:** 0 success · 1 runtime error (logged) · 78 refusal (interpreter missing/below floor, live-install path via `expanduser+resolve+samefile` per ledger_check.py:46-55/90-97, torn journal `JSONDecodeError`, schema drift, malformed argv) · 2 argparse-default only.

**stderr (one greppable line, token-pinned):** `splice: ERROR: <op> <args…>: <reason>` / `splice: REFUSED: <token>: <detail>`.

**Serialization rule (C-6):** `json.dumps(d, separators=(",", ":"), ensure_ascii=False)` + one trailing `"\n"`; `open(tmp,'w',encoding='utf-8')`, write, flush, **no fsync**; `tmp = jp.with_name(f"{name}.tmp.{pid}.{uuid4hex}.{msecs}")`; `os.replace(tmp, jp)`; best-effort tmp unlink on OSError. Invariants: **key order from input; non-target field values byte-identical.**

**Preflight probe (C-7):** in promote.sh BETWEEN `lock_heartbeat` (:177) and `journal_init` (:183), before any destructive step: `python3 -c "import sys,json,os,pathlib; assert sys.version_info >= (3,7)"` — missing/short → `_warn` + exit 78. Journal an informational probe-OK event (interpreter path included). Floor 3.7 is sufficient (pathlib.samefile + dict-order guarantees). Stdlib-only; bare `python3` from PATH; PLUGIN_STALENESS_PYTHON chain deliberately NOT used (that chain exists for PyYAML deps the helper doesn't have).

---

## 4. Plan Corrections (developer must receive)

| # | Sev | Site | Correction |
|---|-----|------|------------|
| C-1 | 🔴 | decisions.md:206 / phase3 D-P3.1 / T-G2 cells | Cap-reach requires `consecutive_green ≥ 3` for SUCCESS, else exit 1 → auto-rollback. Delete the "floor+cap" clause. (Never-green commit bug under sustained DB churn.) |
| C-2 | 🔴 | decisions.md (a)+(b) / phase2 D-P2.6 | Add `set-in-flight-field` op; heartbeat writes NESTED `in_flight.last_heartbeat` (top-level `set-field last_heartbeat` is invisible to the sweep — its only reader). |
| C-3 | 🔴 | phase2 T2.4 | REPLACE the bash `journal_heartbeat` call (lib.sh:1196), do not augment — else the 139s walk stays on the 30s loop (>900% duty) and (b)'s perf premise is void. |
| C-4 | 🔴 | phase1 D-P1.4 | boot_sweep previous-write source = `in_flight.displaced_version` captured at `journal_open_txn` (bash whole-object write; no helper dep), NOT `journal.previous` (= N-2). Old-format txn → halt-for-human. |
| C-5 | 🔴 | phase1 D-P1.6 | Comment refresh extends to lib.sh:69-78 (LIVEZ_BUDGET_S/READYZ_BUDGET_S comments), not just promote.sh:21-23. |
| C-6 | 🟡 | risk-register R-02 / decisions (a) | Reframe byte-identity invariant: key-order-preserved + non-target-values-byte-identical under the pinned compact serialization (§3). Literal "untouched regions byte-identical" is unachievable under whole-file re-dump. |
| C-7 | 🟡 | phase2 D-P2.1 | Explicit `python3 ≥ 3.7` floor assert in the preflight probe; probe placed between promote.sh :177 and :183; probe result journaled. |
| C-8 | 🟡 | phase3 D-P3.4/e | (a) classify via `curl -sS -o tmp -w '%{http_code}'` — `curl -f` suppresses bodies on exit 22; (b) Q5 resolved: busy-amnesty reads EXISTING readiness `detail.inflight_turns` (readiness.py:198/222); no /run file; do NOT reuse `has_instance_busy`. |
| C-9 | 🟡 | test-strategy T2.9 / AC4a fixtures | Fixtures must write `in_flight.last_heartbeat` via the splice path; T2.9's "first-ever writer for `owner_heartbeat_at` proven" contradicts D-P2.9 (zero-writer preserved) — test-contract drift class. |
| C-10 | 🟡 | phase3 D-P3.5 + runbook | (a) worst-case arithmetic = 870s (30s slack) via dynamic soak squeeze — update the table; (b) post-stop deadline breach leaves daemon DOWN: halt detail carries daemon-state + runbook first-recovery-step; (c) add `deadline-breach-<phase>` reason token. |
| C-11 | 🟡 | phase4 / runbook §7 | Maiden §7 must include: blob-prune drain watch, post-promote real-message smoke (mandatory), transient indicator list, re-stage ENSEMBLE_BINARY_VERSION rule, no-key-rotation window, nonce + overnight boundaries. |
| C-12 | 🟡 | phase4 P4.1 | Record merge-or-defer decision for `feature/checkpoint-conn-resilience` (base 0c040e5f8) in the v0.20.1 vehicle; verify merge base against c8a3fbf6. Silent non-inclusion = stakeholder surprise. |
| C-13 | 🟢 | decisions (b)/(e) | Bind grace wording (≤ enclosing phase budget + 900); optional executor_exit-with-null-op WARN canary; fence `open-txn` op out of P2; document the R-10 ≤540s relaxation proof in decisions.md. |

---

## 5. Phase Sequencing — CONFIRMED

P1 → P2 → P3 → P4 as planned. Verified: P1 is genuinely independent of the splice helper (launcher boot_sweep stays pure bash; C-4 preserves that — the displaced_version stamp is a bash whole-object write); P2 entry-gated on ENVSIM_BASELINE timing capture before the splice lands; P3 depends on P2 (set-in-flight-field op for new journal writes; sweep changes ship in P2); P4 integrates + stages. `_json_field_quoted` precedent already at HEAD — no P1 ordering hazard. The 900s budget/`COOLDOWN_S=600`/`SWEEP_STALE_S=600` interactions are covered by §2.2's proof (lock-held defer + fresh-heartbeat + in_flight gate); budget counts lock-wait in.

---

## 6. Aggregated Risks (deduped, severity-ordered)

- 🔴 Never-green soak commits under sustained DB churn (C-1) — correctness inversion of today's semantics; certain to matter once the checkpoint_saver 503 probe merges.
- 🔴 Heartbeat invisible to its only reader (C-2) — decision (b)'s entire purpose silently fails; caught only by fixture correction C-9.
- 🔴 139s walk retained on the 30s heartbeat loop (C-3) — perf premise void; ceremony stays slow.
- 🔴 boot_sweep writes N-2 as previous (C-4) — wrong manual-rollback target after boot-sweep commit.
- 🟡 FORWARD/BACKWARD `previous` semantic undocumented (ADR needed); test-fixture field drift (C-9); post-stop breach strands a stopped daemon (C-10b); vehicle branch reconciliation (C-12); key rotation during ceremony window; stop worst-755s → halt trade (intended, must be explicit).
- 🟢 stderr tokens, WARN canary, tmp cleanup, R-10 relaxation documentation.

---

## 7. Decisions Pending (leader/operator)

1. **ADR: `journal.previous` = FORWARD semantic** (version displaced by the most recent terminal event) — implemented reality across all three writers; document + status-display note. No code change.
2. **D-P1.4 old-txn fallback:** recommended halt-for-human for pre-change-format in_flight txns; confirm in PR-1 review (Q4-adjacent).
3. **checkpoint-conn-resilience merge-or-defer** for the v0.20.1 vehicle (C-12).
4. **Maiden mode record:** FAST per leader ruling — validated; record per risk-register §4 sign-off item (2) with §7 compensating controls (C-11) attached.

## 8. Open Questions

- Does `feature/checkpoint-conn-resilience` merge cleanly onto c8a3fbf6? (Unverifiable from this worktree.)
- Real soak-heartbeat wall cost vs the 139s @87KB fixture figure (direction unaffected; ENVSIM_BASELINE will pin it).
- Cgroup-leaf matching behavior on non-systemd hosts (plan assumes rule-3 path; not code-verified).
- Displaced-version capture site: `journal_open_txn` (recommended — precedes any flip, already a whole-object writer) vs intent_flip site.

## 9. Confidence

**High** on C-1…C-8 (direct file:line evidence; C-1 and C-2 independently cross-confirmed by two workers each). **Medium** on the C-4 old-txn fallback semantics and C-12 branch reconciliation (one-window edge / not visible from this worktree). Assumption that would flip the headline recommendation: none — the corrections are additive to the plan's architecture; if any single correction were rejected, the corresponding risk (§6) transfers to the runbook/watch layer rather than disappearing.

---

*Instance IDs: cebbbc85-402a-40c8-a886-9224d39fb795 (data-flow-design), eabe3fac-7f11-426a-8da5-002bcf027e07 (resilience-design), ee8ccaff-d640-41ac-8f39-6049e9358ebc (resilience-design). Aggregated and written by the architect controller.*
