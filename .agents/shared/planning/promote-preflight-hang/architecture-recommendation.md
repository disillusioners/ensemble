# Architecture Recommendation — promote-preflight-hang (board wi-fc1786a2)

**Date:** 2026-10-29 · **Architect instance:** this lane's architect (controller)
**Analyst instances:** `0d4a715f` (resilience-design, watchdog) · `60a29913` (structural-design, read path + parity) · `d08cc692` (data-flow-design, logging + deployment truth)
**Base rev:** `b1183a637` (all anchors verified at TIP by the analysts; MEASURED items were run in mktemp scratch on synthetic fixtures — no live contact, no daemon boot, no `env -u POSTGRES_*`)
**Status:** COMPLETE — rulings below are binding on the planner; plan amendments are numbered in §5.

---

## §0 Method

Three parallel skill-per-worker analyses over the plan set + repo (read-only): resilience-design on the watchdog
(OQ1 + breach semantics + exit code + budgets), structural-design on the read path (OQ2 + parity pins + the
25-site census), data-flow-design on logging + ceremony-time deployment truth + sibling-lane coordination.
Evidence was adjudicated: every load-bearing claim below is either code-verified at TIP or MEASURED, and each
ruling names its evidence. Where the three reports converge (the scripts-dir hazard appears independently as
Worker A's R18 and Worker C's R-A), the finding is treated as established.

---

## §1 THE RULINGS

### RULING 1 (OQ1) — Watchdog final shape: **ACCEPT the recommended L2a + L1 + L3(backstop) + L4(routed), with mandatory amendments**

The planner's layering is correct and survives adversarial analysis:

- **L2a (per-step `_run_bounded`) is the only layer that fires inside the 2026-10-28 step shape** (`J="$(journal_read)"`, `promote.sh:225`). House-native (`lib.sh:3531-3557`, 5 sibling precedents), and its rc collapse (137|143→124, `lib.sh:3551-3553`) is a clean breach discriminator.
- **L1** (`_enforce_deadline`, `lib.sh:255-282`) reused verbatim — the belt.
- **L3 is a backstop and must be *derived* and *kind-scoped*** — see RULING 6. Measured/experimental proof that L3's kill path is sound is in RULING 5.
- **L2b correctly dropped** (no site needs it post-L2a).
- **L4 stays ROUTED** — see RULING 3.

**Five-axis comparison of the contested shapes** (evidence: Worker A §1–§2):

| Shape | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **L2a-primary + L1 + L3-backstop + L4-routed** (recommended) | Med — wraps at enumerated sites, house primitive | High — budgets per step scale with evidence re-measurement | High — reuses `_run_bounded`/`_enforce_deadline` verbatim | Low-Med — false-fire risk concentrated in 2 budget numbers (fixed by RULING 6) | Low | ✅ **ACCEPTED** |
| L3-primary (RuntimeMaxSec as the mechanism) | Low | Low — one wall-clock number for all failure classes | Med | **High** — blunt kill, no step attribution; fires exit-143 not 78; can fire mid-soak post-flip (R17) unless derived; PID-visible blind before it fires | Low | Rejected as primary; kept as backstop |
| Pull L4 (daemon reaper) into this lane | High — daemon reaper contract + liveness predicate + vocabulary in a shell lane | Med | Low — cross-subsystem coupling in one diff | **High** — 660s is a healthy-path number; timer-kill kills legitimate promotes; pre-txn predicate doesn't exist yet | High | Rejected — routed (RULING 3) |

**Mandatory amendments folded into the shape** (from Worker A; the planner MUST fold A1–A16 in §5):
single-`halt`-row guard (re-entrancy), breach order journal→warn→release→exit (the splice fence makes the order
load-bearing), unlocked-path breach split, trap-install-before-`lock_acquire`, RuntimeMaxSec kind-scoping +
derivation, batched tree walk before the stage budget is set, preflight-only arming with a real-soak negative control.

### RULING 2 (OQ2) — Read-path policy: **ACCEPT P1 — dedicated read-only helper, new sibling file `scripts/upgrade/journal_read.py`**

- **P1 over P2:** `journal_splice.py`'s refusal surface is 26 `_die_refused` sites across 8 token classes, pinned by the house `test_journal_splice.sh` pack; a read verb routed through its `main()` must bypass three write-protective gates (live-install `:549-560`, missing-journal `:564-568`, strict-parse `:298-302`) — every bypass re-opens a mutation-verified pin surface. P1's new file touches none of it.
- **P1 over P3:** P3 changes the call order of `rollback.sh`/`restart.sh` (both explicitly comment *"the lock is not held yet at this point"* at `:73-77`/`:120-124`) and special-cases `status.sh` — larger diff, larger blast radius, zero benefit.
- **Policy of the helper:** reads are not mutations ⇒ **no live-install refusal**; absent/empty/torn ⇒ rc 1 + the same stderr wording class as today; the *write* fence in `journal_splice.py` is untouched.
- **Hard constraint (new):** the read path is **forbidden to route through `_splice_journal`** — that path remaps rc 78→1 (`lib.sh:1174`) and merges stderr into stdout (`:1172`), breaking both the rc and the stdout contract. The helper gets its own thin path resolver (mirror `_splice_journal_path`, `lib.sh:1134`).
- **Fallback:** the bash scanner body stays verbatim in `lib.sh` as the no-interpreter fallback (D1-7), with the distinct warn line. `status.sh` gains the floor probe under every option.

**Five-axis comparison:**

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **P1 dedicated helper** | Low — one new file, zero call-order changes | High — all 25 sites unchanged | High — one concern per file (mirrors `ledger_check.py` precedent) | Low — write fence untouched | Low | ✅ **ACCEPTED** |
| P2 read-verb on journal_splice.py | Med | Med | Med — bypass branches in delicate refusal machinery | Med-High — re-opens pinned refusal surface | Med | Rejected |
| P3 fence-gate reads | High — call-order changes in 2 scripts + exemption | Low | Low | High — changes rollback/restart sequencing on safety paths | High | Rejected |

### RULING 3 (OQ3 / D4b) — **CONFIRM ROUTING to its own commission**

The benign-detach no-kill is load-bearing (the executor is *designed* to survive daemon death — the whole point
of the transient unit, `upgrade_journal.py:2664-2677`); 660s is a **healthy-path** number (490s observed minimum
+ headroom, `sweep.py:98-102`), not a stall number; and the daemon's liveness predicate
(`_executor_alive_recent`, `sweep.py:2008-2059`) keys on the txn heartbeat, which exists only after
`journal_open_txn` ⇒ **a pre-txn stall — the exact 2026-10-28 shape — is invisible to it**. Building the pre-txn
signal is a daemon-side design task with its own gate profile.

**The routed commission's mandatory scope (handoff, fold into the board item):**
1. A **pre-txn stall signal** — a "preflight started" marker published *before* `journal_open_txn` (D3's per-run
   log + phase markers are the raw material — the marker must exist before the txn, not after).
2. The **"stalled" predicate** (no journal progress AND no heartbeat advance for N s) — never a bare timer.
3. The **writer-side journal-growth policy** (89% of the 455 KB journal is `mcp_server_state`,
   `daemon/mcp/init_guard.py:150` — D1's size guard only converts the cliff to a loud refusal; the growth itself
   is the owner's to fix).

In-lane, D4b reduces to correlatability only (the `executor_still_running` row carries the per-run log path +
last phase marker). Pulling the kill contract in would grow the lane from "shell + one daemon log-path change"
to "daemon reaper contract change" — a different commission.

### RULING 4 — Exit code and journal vocabulary: **keep exit 78; the row kind is `halt`; the discriminator is kind + detail-prefix + exit code**

Decisive evidence (Worker A §2):
- Terminal classification is **by journal row kind, never by exit code**: `_TERMINAL_EVENTS` = {`halt`, `refusal`, `executor_exit`} (`upgrade_journal.py:1799-1809`); `reconcile_pending_op` closes the pending_op on the first terminal-kind row after `armed_at` (`:1943-1990`); the reaper *records* `exit_code=<n>` without classifying on it (`sweep.py:2261-2272`).
- 78 already spans two semantics today (`_refuse`→`refusal` row vs `_enforce_deadline`→`halt` row, `lib.sh:5597-5614`/`:277-281`) — so no operator surface may read exit-78 alone as "stage-guard refusal"; the runbook amendment is binding.
- **A new row kind would dangle the pending_op** unless added to all three registries (`_TERMINAL_EVENTS` + `ALERT_KIND_BY_EVENT` `upgrade_journal.py:643-649` + `upgrade_tools._OUTCOME_LABELS`). Not worth it: `halt` with detail `deadline-breach-<step> (budget=<name> value=<n> elapsed=<n>s phase=<p>)` closes the op and files under the existing `upgrade_cap_halt` alert kind.
- **A new exit code (75/79) buys nothing**: 78 is in `_LAUNCHER_CLEAN_EXITS` (`upgrade_tools.py:1823`) so the launcher never treats it as a crash.
- **Two breach classes, documented as distinct:** shell watchdog breach = exit **78** + `halt` row **with** `deadline-breach-` prefix; L3 systemd kill = exit **143** + `halt` row **without** the prefix (the TERM trap journals "killed by SIGTERM"). Runbook step: `systemctl show ensemble-upgrade-<run_id> -p RuntimeUSec -p RuntimeMaxUSec`.

### RULING 5 — Breach semantics vs the trap machinery: **ENDORSED with a mandatory invariant and three guards**

The commission's central worry — "does RuntimeMaxSec's kill flow through the same TERM handler, or does the
watchdog itself orphan the lock?" — is answered **by experiment** (Worker A):

1. **A PID-only TERM is trap-bypassing** (MEASURED): bash defers the TERM trap while blocked in a command
   substitution — the shell stays alive, zero trap output. *A PID-targeted kill would have reproduced the
   incident silently.*
2. **A cgroup/process-group-wide TERM fires the trap** (MEASURED): the substitution subshell dies, the
   substitution completes, the main shell's pending trap fires — exactly the 2026-10-28 operator-TERM mechanism
   (journald `Stopping` 17:42:44.415964 → `halt` row + lock release → `status=143` at .590524, **175 ms**).
3. **systemd's kill is cgroup-wide by default**: the executor unit sets no `KillMode`/`KillSignal`/
   `TimeoutStopSec` (`upgrade_journal.py:2501-2537`) ⇒ host defaults `control-group`/`SIGTERM`/`90s`.
   **RuntimeMaxSec uses the identical stop path ⇒ the trap fires.** Phase 0 must pin this with a runtime probe
   on a sandbox transient unit (A12) — documented default, not yet locally proven.

**🔴 DESIGN INVARIANT (record in the plan, verbatim):** *any mechanism that kills a stuck executor — L3, the
future L4 reaper, an operator drill — MUST deliver the signal to the whole cgroup/process group. A PID-targeted
kill is trap-bypassing: no halt row, no lock release, and the orphan is only recoverable via the dead-owner
stale-break.* (`lock_acquire` self-heals a dead owner, `lib.sh:1689-1694` — the unrecoverable case is a
SIGKILL-class death with a live-looking lock, which is D4a's lane.)

**Three mandatory guards:**
- **`_HALT_JOURNALED` re-entrancy guard** on `_signal_journal_halt` (MEASURED: a 2nd TERM during the handler
  re-enters it ⇒ duplicate `halt` rows; `_LOCK_RELEASED` protects the lock, not the journal). Pin with a
  double-TERM test.
- **Breach order is load-bearing: journal row → warn → `lock_release` → exit.** The live-install splice fence
  refuses journal writes when the pipeline lock is not held (`journal_splice.py:549-560` + `_pipeline_lock_held`
  `:158-180`) — release-first would silently lose the row. Pin on a live-shaped path.
- **Unlocked-path breach split:** `rollback.sh:90`/`restart.sh:133`/`status.sh:52` hold no lock ⇒ their breach
  rows would be fence-refused. Behaviour: warn + per-run log only, **no journal row**; `status.sh` exits **1**
  (a read-only status tool never mutates the journal, never exits 78). **Amend AC2** — "exactly one journal row"
  is achievable only on locked paths.
- **Trap-install window** (`promote.sh:170` lock-acquire → `:179` trap-install): move
  `_trap_install_signal_handlers` **before** `lock_acquire` (the handler is a no-op without a lock).
- **Child-row tolerance:** function-children do *not* inherit the TERM trap (bash resets caught traps in forked
  subshells — no spurious rows); **external-script children with their own handlers** (e.g.
  `scripts/stop-ensemble.sh:275`) *do* write their own `halt` row when TERMed — the breach handler must tolerate
  a pre-existing child row, and their `lock_release` is already refused by the owner check (`lib.sh:1751-1756`).
- **`unit_properties` plumbing gap:** `build_service_argv` accepts `unit_properties` (`:2468`) but the spawn
  call site does not pass it (`:2738-2741`) — L3 needs this plumbing, kind-scoped (RULING 6).

### RULING 6 — Budgets: **shape endorsed; two numbers CONTESTED and corrected; scope = preflight + stage trees + derived outer bound**

**(a) Scope.** Per-step/per-phase budgets apply to the **promote preflight** (steps `promote.sh:225–:373`) and
the **stage tree walks** — armed and disarmed around exactly those windows, never around `gate_soak`
(`lib.sh:5224`, reachable only from `promote.sh:520`, outside the wrapped region). Post-flip phases stay under
the existing 900s ceremony deadline + probe budgets. `rollback.sh`/`restart.sh`/`status.sh` reads get the fast
path post-D1 + the unlocked breach behaviour (RULING 5); no budgets there. The soak exclusion is airtight
against L2a/L1 by construction — **but not against a constant L3** (below).

**(b) 🔴 `STAGE_TREE_BUDGET_S=300s` FALSE-FIRES on a healthy stage — MEASURED.** The plugins tree walk is
**329.6s** (one pass, this host) and stage walks it **twice** (`stage.sh:443` + `:576` re-hash) ⇒ ≈660s > 300s.
Root cause: `_sha256` = `shasum | awk` = **2 process spawns per file** (`lib.sh:420`) inside `_tree_manifest`
(`:2119-2126`). **Fix the constant, not the budget:** batch the hashing (one `shasum -a 256` per tree over many
files) with a **byte-parity pin** against the current per-file output, then size the budget from the re-measured
walk in Phase 0 T0.8 **on the plugins tree**. (The promote-time `integrity_verify` covers only agents+frontend,
`lib.sh:3005` — ≈431 files ≈26s/call, ×2 calls ≈52–60s of the 68s healthy preflight; the 120s step budget is a
~2.3× margin over the *step*, thinner than the plan's "1.8× the whole preflight" framing — re-derive the
justification text.)

**(c) L3 `RuntimeMaxSec` must be DERIVED and KIND-SCOPED, never the constant 1200.**
- **Derived:** `RuntimeMaxSec = max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)`. With defaults:
  max(1200, 900+300+120) = 1320. Rationale: `ENSEMBLE_PROMOTE_SOAK_S` is accepted up to **900**
  (`promote.sh:157-161`) and conservative mode runs the *full* `soak_s` with no ceremony squeeze
  (`lib.sh:5286-5315`) ⇒ a constant 1200 **fires mid-soak, after the symlink flip** — a kill that leaves the
  release flipped, the txn open, and the daemon restarted (Worker A R17). Additionally: **refuse
  `ENSEMBLE_PROMOTE_SOAK_S > 300` when `ENSEMBLE_UPGRADE_LIVE=1`** (the knob stays available for sandbox drills).
- **Kind-scoped:** `spawn_executor` serves **both promote and restart** (`manager.py:5544-5555`,
  `unit_prefix=…RESTART if kind=="restart"`) — pass `unit_properties` **only for `kind == "promote"`**. A
  restart-lane RuntimeMaxSec is a different budget owned by a different ceremony.
- **Healthy-path arithmetic (house, `lib.sh:100-110`):** worst-case default ceremony = 870s < 900s deadline <
  1320s L3 ⇒ in the default mode the ceremony's own deadline always fires before systemd — L3 catches only
  deadline-machinery failure (a wedged shell that cannot fire its own traps), which is precisely a backstop's job.

**(d) Negative controls are upgraded:** the L2a/L1 negative control must include a **real 300s conservative-mode
soak** asserting 0 trip rows AND 0 kills (not a simulated soak-length phase inside the preflight window).

### RULING 7 — Logging architecture (D3): **ENDORSED with a retention rule, an atomicity contract, a sink split, and in-op markers**

- **(a) Retention (the plan's gap — R5 undischarged):** owner = **the shell at ceremony end**, under the lock,
  mirroring `retention_evict`'s seat (`promote.sh:722`). Rule: **count cap keep-newest 20 + age cap 14d**,
  computed with **portable primitives only** (`find -name '*.log' -mtime +N -delete` or python `os.stat`) —
  **never** `stat -f '%m'` (MEASURED: prints filesystem info, not mtime, rc=0 silent garbage on GNU — the exact
  input `lib.sh:5569` feeds it today) and never `_iso_to_epoch`+ISO sorting. **Fences:** never delete the
  current run's file, the `in_flight.run_id` file, the last pointer's file, **any file modified < 6h** (protects
  a *stalled* live run whose mtime is stale — the incident class), or anything while the lock is held by a live
  owner. Daemon-side sweep is optional second-line.
- **(b) Atomicity contract (MEASURED):** one log line = one `write(2)` to an O_APPEND fd; lines **< 8192B are
  atomic** against a concurrent writer; **≥8192B corrupt** (the break is stdio BUFSIZ, not PIPE_BUF). Therefore:
  **hard 4KiB line cap** in the sink (2× margin); longer payloads emit a header + `payload elided, N bytes`;
  **sanitize embedded newlines** (AC1's "exactly one copy" depends on one source line = one physical line —
  `_logv "$LIVEZ_JSON"`/`"$READYZ_JSON"` at `promote.sh:448/:456`, `rollback.sh:245/:250`, `restart.sh:263/:273`
  are unbounded; today 1,258B/73B — latent, a degraded readyz grows). **Per-line fsync REJECTED** (page-cache
  durability covers parent/process death — the stated objective; host crash loses everything else too). Subshell
  rule recorded: writes inside `$( )` land in the file (MEASURED); stdout-mirror suppression is therefore also
  the anti-pollution rule (no caller captures `_log`/`_warn` stdout today — verified).
- **(c) Sink split (architect's call on Worker C's open question):** **TWO files per run.**
  `<install>/data/upgrade/<env>/<run_id>.log` = the shell's timestamped, strictly-ordered narrative (direct
  writes); `<run_id>.payload.log` = systemd's `StandardOutput/StandardError=append:` target for the payload's
  raw stdout/stderr. Rationale (MEASURED): a child's stdout to a regular file is libc **block-buffered** ⇒ the
  `append:` capture is durable but **not real-time** and its bytes can land *after* later markers — in a single
  file that is an ordering illusion that would mislead exactly the post-mortem D3 exists for. Consequences: all
  progress markers are direct file writes, never stdout; the write-failure fallback lands in **journald**
  (systemd's `append:` open of the unwritable file fails ⇒ the payload stream goes to the journal) — AC2 must
  assert journald visibility, and the fallback line carries a distinct `logwrite_failed` marker (a partial write
  yields one truncated + one full copy); AC1's exactly-one-copy is scoped to successful writes. The pointer line
  in `data/upgrade.log` names **both** files, resolved **by `run_id` from the journal**, never "last pointer
  line", and degrades gracefully when retention evicted the file.
- **(d) In-op progress markers (NOT YAGNI, but placed correctly):** step-boundary markers (D3-8) stay; **add**
  an in-loop marker every **N=500 files** inside `integrity_verify`'s walk and `stage.sh`'s manifest-checksum
  walk — post-D1 the journal read is sub-second, but the checksum walks remain minutes-scale
  (measured: plugins ≈330s/pass) and are exactly where a D2 budget trip needs attribution. **Rejected:** a
  background heartbeat subshell (extra writer, orphan risk, may write after the run ends).
- **(e) Change-surface additions (both missed by the plan):** `daemon/services/upgrade_journal_sweep.py:2224-2241`
  (`_log_tail` reads the **aggregate** — after D3-1 the aggregate is pointer-only ⇒ the `executor_exit` row
  loses the run's tail at exactly the wrong moment; it must read the per-run path via `run_id`) and
  `daemon/tools/upgrade_journal.py:2725-2726` (the new `data/upgrade/<env>/` parent needs its own `mkdir`).
  **Reuse `_log_tsl`** (`lib.sh:349-353`, BSD/GNU-portable, write-failure-signalling, already test-exercised)
  rather than a second writer; fix the inverted stream description at `lib.sh:358-360` in the same hunk.
- **(f) Env scoping:** per-run logs live under `data/upgrade/<env>/` — a sandbox ceremony resolves the **live
  install root** via the frozen-binary path derivation (`upgrade_tools.py:687-695`) and must not pollute the
  live tree's audit/retention domain. Redaction: DB name + port in the per-run file are **status quo** (today's
  aggregate already carries them); T3-10 additionally asserts no cross-env mixing and no secrets in
  pointer/provenance rows.

### RULING 8 — Parity pin strategy for D1: **the corpus grows 15→22; parity is defined against `$(cat)`-processed bytes; three algorithms, not one**

The byte-level port is the right approach, but Worker B's measurements redefine what "byte-level parity" means:

- **🔴 The `$(cat)` emulation obligation (new, binding):** `journal_read`'s stdout is
  **command-substitution-processed bytes**, not file bytes: ALL trailing `\n` stripped (MEASURED: a canonical
  74B journal echoes as 73B — *every real journal ends in exactly one `\n`*, `journal_write` `printf '%s\n'`
  `lib.sh:1007`) and **NUL bytes silently dropped** (MEASURED: `{"a":"x\0y"}` echoes as 10B, with bash's
  `warning: command substitution: ignored null byte` as a third stderr class). A literal raw-dump helper **fails
  stdout parity on every real journal**. The helper's stdout contract is: *emulate bash command substitution*
  (strip trailing newlines, drop NULs, emit the documented stderr warning when NULs were dropped).
- **🔴 Duplicate-key `journal_update` corruption is BASE behavior (MEASURED):** on
  `{"x":10,"b":2,"x":20}`, `journal_update x 99` produces `{"x":10,"b":2,"x":99,"b":2,"x":20}` — the head
  splices at the **LAST** occurrence (`${rest%*"${field}"*}`, `lib.sh:1039`) while the value scan resumes after
  the **FIRST** (`:1040-1042`). The port must **pin these exact corrupt bytes as BASE-bug-preserving** — an
  un-pinned parity test would flag the faithful port as defective, or invite a silent "fix" = a behaviour change
  on the RMW path. (A real fix is a separate, explicitly-adjudicated change; not this lane.)
- **🟡 Three distinct algorithms, not one "same state machine":** (i) `journal_read` = string-aware
  balance scan (`in_str`/`esc`); (ii) `_json_sub` = **string-naive** first-occurrence bracket counter (no
  string awareness — a bracket inside a value legitimately redirects it today; also quadratic *twice*:
  `out="$out$char"` AND `rest="${rest:1}"`, `lib.sh:589-618`); (iii) `journal_update` = string-naive first-match
  value consumer with a **2-character** escape skip (`${rest:2}`, `:1050` — vs a byte port's 2 bytes; divergent
  only on invalid-UTF-8+escape, which the corpus now covers). **Forbid sharing the `in_str` machine across
  verbs** — that changes the extraction set.
- **Corpus 15→22:** (16) whitespace-only ⇒ ACCEPT+echoed; (17) newline-only ⇒ EMPTY-class refuse; (18) valid
  JSON + brace-free trailing garbage ⇒ ACCEPT (MEASURED — the scanner scans the whole string); (19) valid JSON +
  brace garbage ⇒ REFUSE; (20) NUL-in-string (A/contract above); (21) duplicate-key journal — read ACCEPT,
  `_json_sub` FIRST occurrence, `journal_update` exact corrupt bytes; (22) escape-then-multibyte under BOTH
  `LC_ALL=C` and `en_US.UTF-8`, plus one invalid-UTF-8+escape row. Plus a guard-boundary trio
  (threshold−1/+1) wired to the T1.6 env constant. The real 455,783-byte fixture (or same-shape synthetic)
  remains the timing leg.
- **Differential harness:** true BASE is the oracle for all small cases (cheap); the chunked bash variant
  (Option D) may proxy for BASE on the 455KB fixture **only after a proxy-validation leg** (proxy ≡ true BASE on
  the full corpus + one 32KB fixture where true BASE ≈13s); retain the bounded-60s true-BASE
  "did-not-complete" symptom leg; timing claims cite only true-BASE/HEAD numbers.
- **Divergence boundaries that need pins, not docs:** trailing-`\n`/NUL contract (above), duplicate-key bytes
  (above), the `${rest:2}` char-vs-byte cell (corpus 22). **Documentation-only (verified non-issues):** no
  global locale pin exists but the verdict is locale-invariant for valid multibyte (MEASURED both locales,
  byte-identical); `printf %s` used throughout (87 vs 3 bare `echo`), no `xpg_echo`/posix mode; no
  bash-version-sensitive constructs.
- **Census amendments:** 25 sites recounted exactly (17 lib.sh + 8 scripts). Add failure-mode-class and
  stderr-contract columns (safety-critical: `promote.sh:225`, `promote_entry_check :5630`, RMW sites,
  `rollback.sh:90/:157`; loud-abort: `promote.sh:558`, `restart.sh:133`; tolerant: `:1560`, `:5703`, `:1876`,
  `:1511`, `:1086`, `:1406`; cosmetic: `status.sh:52`, `retention_evict :5528`). **No `set -e` anywhere**
  (`set -u` only) — `promote.sh:276`/`:588` continue-on-empty; the helper must preserve rc *exactly* there.
  Two sites deliberately suppress stderr (`:1803`, `:1876`).

### RULING 9 — Ceremony-time efficacy: **🔴 the fix does not reach the ceremony unless the scripts-dir truth is handled; the guard's seat is the DAEMON, not the scripts**

**Measured deployment truth (Worker C's numbered chain, verified):**
1. The scripts dir is resolved **once, at ARM time, in the daemon**: `_resolve_scripts_dir`
   (`upgrade_tools.py:1953-1966` chain: `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → module-repo → `install_dir/scripts`)
   → frozen into the arm marker (`:3714`).
2. `manager.py:5494-5496` builds `bash <scripts_dir>/promote.sh …` — the path is in **argv**, not executor env
   (the env var is stripped by `EXECUTOR_ENV_ALLOWLIST`/`PREFIXES`, `upgrade_journal.py:2156-2162` — and doesn't
   need to reach it).
3. Release payloads ship **no `scripts/`** (v0.27.4+; `stage.sh:392-425` copies only binary+agents+frontend+
   plugins+config+launcher) ⇒ **a checkout is the only scripts source on this host**.
4. MEASURED: `/home/nea/agents-ensemble/.env:107` → `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`;
   dev checkout HEAD `013895287` = **277 commits behind** `b1183a637`; `scripts/` tree clean; **dev
   `lib.sh:811` still contains the O(N²) scan**. `origin/latest` == `b1183a637` (no drift).
5. **⇒ The next ceremony runs the OLD code unless the checkout is synced or the pin moved.** D1–D4 are then a
   no-op at ceremony time; worse, L2a/L1 (shell-side) are absent while only L3 (binary-side) would be present —
   the primary/backstop relationship inverts (Worker A R18).

**RULING — the minimal safe answer is four pieces, and the guard's seat is corrected:**

- **(i) Hard precondition (runbook + plan §"Ceremony readiness"):** before the next ceremony, sync
  `/home/nea/ensemble-src/scripts/upgrade/` to ≥ this lane's merge rev (scoped to `scripts/` — the checkout is
  dirty in 15 unrelated files; the WIP owner reconciles the rest) or move the pin. **Leader/user-owned action —
  this lane is STAGE-ONLY and must not touch the dev checkout.**
- **(ii) Journal-always provenance at ARM time, seat = the daemon** (`upgrade_tools.py` arm path — the daemon
  binary is the one component whose version the promote itself guarantees): record `scripts_dir`, `lib.sh`
  sha256, and git rev + dirty flag (if the dir is a git checkout) in the arm journal row. Pure observability,
  zero refusal risk.
- **(iii) Version stamp + floor check, seat = the daemon (the seat correction):** `lib.sh` carries
  `PIPELINE_SCRIPTS_REV=<this lane's merge commit>` (echoed at executor start into the journal row + the
  per-run log's first line). The **daemon** records `MIN_PIPELINE_SCRIPTS_REV` and, **at arm time, before the
  nonce is consumed**, compares: stamp absent-or-older-than-floor on an **explicitly pinned** scripts dir ⇒
  **refuse to arm** (refusal row naming the sync command); dirty git checkout ⇒ journal WARNING; fallback-dir
  resolution (module-repo / `install_dir/scripts`) without a stamp ⇒ WARNING only (backward compat — the refusal
  applies exclusively to the explicit `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin, which is a deliberate operator
  choice). Escape hatch: `ENSEMBLE_ALLOW_STALE_SCRIPTS=1`, journaled loudly.
  **Why the daemon and not promote.sh/lib.sh:** a stale lib.sh has no stamp and no check-code — *a scripts-side
  guard structurally cannot catch a stale scripts dir*. The checking code must live in the component the
  promote itself versions — the binary. (Worker C's A6 placed the refusal in the scripts; corrected here.)
- **(iv) Defense-in-depth (Worker A A14, kept):** the executor env carries
  `ENSEMBLE_PROMOTE_WATCHDOG=1` (add to `EXECUTOR_ENV_ALLOWLIST`); `promote.sh` **refuses** (loud `refusal` row)
  when the token is present but the watchdog is not armed — catches mixed-generation deployments (new binary +
  half-synced scripts). Old scripts ignore the token; (iii) catches those.
- **Sequencing constraint (Worker C R-G):** any scripts-dir refusal must fire **before the nonce burn**
  (`consume_pending_action`) — else the guard consumes the ceremony it protects.

### RULING 10 — Sibling-lane coordination: **no code conflict today; three standing rules**

MEASURED: the event-loop sync-DB lane's worktree (`ensemble-src-wt-eventloop-sync-db-hotpaths`) sits at base
with **zero commits**; its declared surface (`daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`)
has **zero overlap** with this lane's files. Rules: (1) version slot re-derived by live census at stage time
(house policy — unchanged); (2) **L1 tip-identity re-check at stage time** — if the sibling advances
`origin/latest` first, our stage must re-derive from the new tip or be refused (exit 78, non-tip tree); re-stage
only per house precedent if drift is code-bearing; (3) if either lane later touches
`upgrade_journal.py`/`upgrade_tools.py`, re-verify the other's pins. Never hand-edit `state.json` for slot
arbitration.

---

## §2 Risk register ADDITIONS (beyond the planner's R1–R11)

| ID | Risk | Sev | Disposition |
|----|------|-----|-------------|
| **R-A** | The fix does not reach the ceremony: pinned scripts checkout is 277 commits behind and still carries the O(N²) scan (MEASURED); shell-side watchdog absent, L3-only present — primary/backstop inverted | 🔴 | RULING 9 (precondition + daemon-seat provenance + floor refusal + watchdog token) |
| **R12** | `STAGE_TREE_BUDGET_S=300s` false-fires on a healthy stage (plugins walk measured 329.6s/pass, walked twice ⇒ ≈660s) — aborts a good stage | 🔴 | RULING 6b: batch the hashing + derive the budget from the re-measured walk |
| **R13** | Any PID-targeted kill is trap-bypassing (MEASURED): no halt row, no lock release — a future daemon reaper or operator drill can silently reproduce the incident | 🔴 | RULING 5 invariant: cgroup/process-group-wide signals ONLY, pinned in Phase 0 |
| **R14** | TERM-trap re-entrancy ⇒ duplicate `halt` rows (MEASURED); the journal is not idempotent | 🔴 | RULING 5: `_HALT_JOURNALED` guard + double-TERM pin |
| **R15** | Breach journal rows are unachievable on unlocked paths (the live-install splice fence refuses them); `status.sh` must never mutate the journal | 🔴 | RULING 5: unlocked-path split; AC2 amended; `status.sh` exits 1 |
| **R-N1** | Parity is defined against `$(cat)`-processed bytes (trailing-`\n` stripped, NULs dropped — MEASURED); a raw-dump helper fails stdout parity on every real journal | 🔴 | RULING 8: command-substitution emulation contract + corpus cases |
| **R-N2** | Duplicate-key `journal_update` corruption is BASE behavior (MEASURED); an un-pinned parity test flags the faithful port as defective or invites a silent behaviour change | 🔴 | RULING 8: pin the exact corrupt bytes as BASE-bug-preserving |
| **R16** | `RuntimeMaxSec` set at the shared `spawn_executor` also bounds the **restart** unit — a different ceremony with different budgets | 🟡 | RULING 6c: kind-scoped to `kind=="promote"` |
| **R17** | `ENSEMBLE_PROMOTE_SOAK_S` accepted up to 900 ⇒ constant L3=1200 fires mid-soak **after the symlink flip** | 🟡 | RULING 6c: derived RuntimeMaxSec + refuse SOAK_S>300 in live mode |
| **R-B** | Log lines ≥8192B corrupt under the concurrent writer (MEASURED; livez/readyz JSON logs are unbounded) | 🟡 | RULING 7b: 4KiB cap + elision + newline sanitization |
| **R-C** | Payload stdout is block-buffered ⇒ not real-time in the `append:` capture — ordering illusion in a single file | 🟡 | RULING 7c: two-file sink; markers are direct writes |
| **R-D** | Retention can delete a stalled live run's file (stale mtime; systemd holds it open ⇒ narrative vanishes into an unlinked inode) | 🟡 | RULING 7a fences (<6h mtime, in_flight, pointer, lock-held) |
| **R-E** | The sweep's `_log_tail` reads the aggregate D3-1 abandons ⇒ `executor_exit` loses the run's tail | 🟡 | RULING 7e: sweep reads per-run path via run_id |
| **R-F** | A sandbox ceremony resolves the LIVE install root (frozen-binary path derivation) ⇒ per-run logs pollute the live audit/retention domain | 🟡 | RULING 7f: env-scoped `data/upgrade/<env>/` |
| **R-G** | A scripts-dir guard firing after the nonce burn consumes the ceremony it protects | 🟡 | RULING 9 sequencing: refusal before `consume_pending_action` |
| **R18** | Shell-side watchdog lives in the unpinned tree; stale checkout promotes with no primary watchdog | 🟡 | RULING 9(iii)/(iv) — merged into R-A disposition |
| **R19** | `_run_bounded` TERMs the direct child only — a grandchild survives; harmless for read helpers, fatal if the wrapper is reused on a write path | 🟡 | RULING 5/A1: wrapper contract note; process-group kill for side-effecting steps; never reuse on write paths |
| **R20** | Trap-install window (lock held, no handler) ⇒ TERM there orphans the lock with no row (the D4a shape) | 🟡 | RULING 5: install traps before `lock_acquire` |
| **R-N3** | "Same state machine" phrasing invites sharing the `in_str` scanner across all three verbs — changes the extraction set | 🟡 | RULING 8: three-algorithms rule, per-verb pins |
| **R21** | `_run_bounded` collapses a child's legitimate 143/137 to 124 | 🟢 | Document in the wrapper contract |
| **R22** | Operator docs that read exit-78 alone misfile a watchdog breach as a stage-guard refusal | 🟢 | RULING 4 runbook amendment: read kind + detail |
| **R-H** | `_log_tsl` already exists (portable, failure-signalling, unwired); `lib.sh:358-360` inverts the stream description | 🟢 | RULING 7e: reuse; fix doc in the same hunk |
| **R-I** | `_json_sub` is quadratic twice (`out="$out$char"` AND `rest="${rest:1}"`) — the census must not over-claim SC3 coverage | 🟢 | RULING 8: both mechanisms in the port scope |

---

## §3 Approach notes the planner must NOT re-litigate

- Strict-JSON read (D1 Option B) stays rejected — a stricter read refuses files the current read accepts ⇒
  ceremony behaviour change on a safety path, not required by the incident. The asymmetry is already recorded at
  `journal_splice.py:273-275`.
- The journal format split (D5-7) stays out — migration-shaped; recorded as the follow-up candidate for the
  MCP/init-guard + journal owners.
- `_json_escape`/`_provenance_sidecar_ok` stay bash (short inputs; ADR-014 portability discipline).

---

## §4 Confidence

**High.** The three contested rulings rest on measured experiments (trap timing, atomicity bounds, walk timings,
`$(cat)` semantics, deployment chain) plus code anchors verified at TIP. The assumptions that would flip them:
(a) the executor unit's effective `KillMode` is NOT the documented default `control-group` (Phase 0 A12 probe
settles it — if it were `process`, L3's soundness collapses and L3 must set `KillMode=control-group` explicitly);
(b) the shasum batch output is not byte-parity-preserving (A8 pin settles it); (c) `ENSEMBLE_PROMOTE_SOAK_S>300`
has live-mode users (unlikely; the knob is documented sandbox-only).

---

## §5 PLAN AMENDMENTS (numbered, actionable — the planner folds these)

**phase2-plan.md (D2):**
1. Add `_HALT_JOURNALED` guard to `_signal_journal_halt`; pin with a double-TERM test (R14).
2. Breach order = journal → warn → lock_release → exit 78; add a live-shaped-path test asserting the row exists (order is load-bearing, R15).
3. Split breach behaviour by lock state; unlocked paths warn + per-run log only, no journal row; `status.sh` exits 1; amend AC2 (R15).
4. Install signal handlers BEFORE `lock_acquire` (`promote.sh:170-179`) to close the trap window (R20).
5. Add `unit_properties` parameter to `spawn_executor`; set RuntimeMaxSec ONLY for `kind=="promote"` (R16; plumbing gap at `upgrade_journal.py:2738-2741`).
6. RuntimeMaxSec = `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)`, computed at arm time; refuse `ENSEMBLE_PROMOTE_SOAK_S>300` when `ENSEMBLE_UPGRADE_LIVE=1` (R17).
7. Replace `STAGE_TREE_BUDGET_S=300` with: batched `_tree_manifest` (one shasum per tree, byte-parity pin vs per-file output) + budget derived from the Phase-0-re-measured **plugins** walk (R12).
8. Arm/disarm the preflight phase budget around the preflight only; upgrade the negative control to a REAL 300s conservative-mode soak asserting 0 trip rows and 0 kills (R1).
9. Breach rows use kind `halt` + detail `deadline-breach-<step> (budget=… value=… elapsed=… phase=…)`; exit stays 78; document the two breach classes (78+prefix vs 143+no-prefix) and the `systemctl show … RuntimeMaxUSec` runbook step (RULING 4).
10. Phase 0: runtime-probe the transient unit's kill properties (`KillMode`/`KillSignal`/`TimeoutStopUSec`); record the cgroup-wide-kill INVARIANT verbatim in the plan (R13); if the probe shows non-default KillMode, set `KillMode=control-group` explicitly on the unit.
11. `_run_bounded` wrapper contract: side-effect-free-on-kill OR process-group kill (`setsid` + `kill -TERM -- -pgid`); never reuse on write paths (R19); tolerate pre-existing child `halt` rows from external-script children; document the 137/143→124 collapse (R21).
12. Breach handler budget justification text re-derived: 120s is ~2.3× the longest single step (integrity_verify ≈26s/call), not 1.8× the whole preflight.
13. Phase 0 adds measured baselines: per-file fork cost, plugins double-walk wall time, agents+frontend walk on the release payload shape (431 files).

**phase1-plan.md / phase0-plan.md (D1):**
14. Helper stdout contract = **emulate bash command substitution** (strip ALL trailing `\n`, drop NULs, emit the NUL-drop stderr warning class) (R-N1).
15. Corpus 15→22 + guard-boundary trio (RULING 8 list); duplicate-key case pins the exact MEASURED corrupt bytes as BASE-bug-preserving (R-N2).
16. State the three-algorithms rule; forbid sharing the `in_str` machine across verbs; cover BOTH `_json_sub` quadratic mechanisms (R-N3/R-I).
17. Differential harness: true-BASE oracle for small cases; Option-D proxy only after a proxy-validation leg; bounded-60s true-BASE symptom leg; timing claims cite true-BASE/HEAD only.
18. Census table gains failure-mode-class + stderr-contract columns; record no-`set -e` and the `promote.sh:276`/`:588` continue-on-empty rc-preservation pins (R-N5).
19. Forbid routing the read helper through `_splice_journal` (rc 78→1 remap, `2>&1` merge — R-N6); placement = new `scripts/upgrade/journal_read.py` (RULING 2).

**phase3-plan.md (D3):**
20. Retention rule (owner = shell at ceremony end under the lock; keep-20/14d; portable primitives only — never `stat -f '%m'`, never `_iso_to_epoch`+ISO sort; five fences) (RULING 7a).
21. Atomicity contract: 4KiB hard line cap + elision marker + embedded-newline sanitization; per-line fsync REJECTED (record why); subshell/stdout-suppression rules (RULING 7b).
22. Two-file sink: `<env>/<run_id>.log` (narrative, direct writes) + `<run_id>.payload.log` (systemd append: target); pointer names both, resolved by run_id from the journal; AC1 scoped to successful writes; AC2 asserts journald visibility on the fallback path (RULING 7c).
23. In-op markers every 500 files inside `integrity_verify` + stage manifest walks; NO background heartbeat subshell (RULING 7d).
24. Change surface: `sweep.py:2224-2241` per-run tail via run_id (R-E); `upgrade_journal.py:2725-2726` mkdir the env-scoped parent (R-F); reuse `_log_tsl`; fix the `lib.sh:358-360` stream-description inversion (R-H).
25. T3-10 adds: no cross-env mixing; no secrets in pointer/provenance rows; the ≥8KiB corruption pin (plant a long line ⇒ RED).

**New plan section "Ceremony readiness" (RULING 9):**
26. The numbered deployment-truth table (scripts dir resolved at arm time; path frozen in argv; env var stripped from executor env; payloads ship no scripts/; the ceremony runs the pinned checkout — today 277 commits stale with the O(N²) scan present, MEASURED).
27. Hard precondition: sync the pinned checkout's `scripts/upgrade/` to ≥ this lane's merge (scoped; the checkout is dirty in 15 unrelated files — WIP owner reconciles) or move the pin. Leader/user-owned; this lane does not touch the dev checkout.
28. Daemon-seat provenance at arm time (scripts_dir, lib.sh sha256, git rev+dirty) — journal-always.
29. `PIPELINE_SCRIPTS_REV` stamp in `lib.sh` + daemon-side `MIN_PIPELINE_SCRIPTS_REV` floor check at arm time, **before `consume_pending_action`**: absent/older stamp on an explicitly-pinned dir ⇒ refuse to arm (sync command in the refusal row); dirty ⇒ warning; fallback dirs without stamp ⇒ warning only; `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` escape hatch, journaled.
30. `ENSEMBLE_PROMOTE_WATCHDOG=1` executor-env token (allowlist addition) + promote.sh refuses when token present but watchdog unarmed (mixed-generation defense).
31. Runbook checklist item: "ceremony scripts provenance verified — the arm row shows the expected stamp."

**plan-overview.md:**
32. §9 assumptions: A4 gains "RuntimeMaxSec is derived, not constant; if the sibling lane changes soak semantics, re-derive"; add A6 "the dev-checkout sync precondition (§Ceremony readiness) is a ceremony blocker owned outside this lane."
33. §5 risk table: append §2's R-A..R-I rows (this document's IDs) or cross-reference them.
34. §6 SC list: SC4's "non-zero exit" stays; add SC4b (unlocked-path breach: no journal row, warn only, status.sh exit 1) and SC12 (ceremony-readiness: a staged sandbox arm with a provably-stale pinned scripts dir refuses BEFORE nonce consumption; with a synced dir, arms cleanly).
35. D4b routed board item text gains the three mandatory scope items (pre-txn signal, stalled predicate, writer-side growth policy).

---

## §6 Evidence discipline

Analyst reports (verbatim, with all MEASURED markers) are the evidence base for this document: resilience-design
(watchdog; experiments 1–4), structural-design (read path; BASE-harness measurement table), data-flow-design
(logging; atomicity/buffering/systemd/deployment measurements). Phase 0 re-measures on the target host what this
analysis extrapolated: real `integrity_verify` wall time on a staged release, the shasum-batch parity, the
transient-unit kill properties (A12), and the plugins walk post-batching.
