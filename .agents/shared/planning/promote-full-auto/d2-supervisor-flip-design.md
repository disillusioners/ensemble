# D2 — Supervisor-Deterministic Promote Flip (Design) — rev 7 (FINAL)

**Status:** Final revision. Approver iteration-003 REJECTED (5 blocking) with the escalation cap reached; the **leader authorized one final iteration with a tight scope** (5 blockers + only the code-/safety-affecting promoted notes). Both approver workers continue to judge the core architecture SOUND; all five fixes are small/mechanical but four carry **code implications**, so they are stated here as implementation requirements.
**Date:** 2026-11-11 (rev 1 council → rev 2–6 review cycles → rev 7 final).
**Repo base:** `edbf3c92f` — approver-verified as base HEAD = latest = origin/latest. PR1 merged (its diff vs `6ca643ad7` = `_resolve_scripts_dir` absolutize + refusal tokens in `daemon/tools/upgrade_tools.py`, runbook, tests, evidence doc); its hunks sit ≥ `:1985`, so the PN-4 anchors (`_OUTCOME_LABELS` `:1255`, region `:1304-1317`) are unaffected.
**Provenance:** council 2/2 consensus; six review cycles; leader-authorized final pass. No live launchd queries; no live-dir contact; no daemon/promote/journal ops.
**Confidence:** High on architecture; **the fully-automatic promote is gated on operator steps not yet committed to** (§15).
**Flip-assumption:** `launchctl bootstrap` honors `RunAtLoad=true` ⇒ bootstrap == start (`scripts/ensemble-prod.plist:109`).

---

## 0. Changelog (final pass)

### Closed in this pass

| Item | Content | Where |
|---|---|---|
| **B1** | comp3 preconditions corrected: comp3 fires ONLY on `hb_stale=1 ∧ owner_dead=1` (launcher.sh:3317-3325; `HEARTBEAT_STALE_S=300` :97, `SWEEP_STALE_S=600` :78). The promote refreshes the heartbeat every loop (lib.sh:2650-2663) ⇒ at SIGKILL it is ≤30 s old ⇒ FRESH ⇒ **comp3 does NOT fire** ⇒ the sweep "leaving alone" (`return 0`) ⇒ the launcher boots the **FLIPPED, possibly-broken `current` SILENTLY**. True end state + recovery path stated; the "comp3 immediate reclaim" claim is **deleted**. Mitigation: launcher reclaim treats OWNER-DEAD as reclaimable even with a fresh heartbeat (bounded wait) + watchdog covers the residual gap | §5.3 case 2, §7, §14 |
| **B2** | The **fourth** 8b branch (promote.sh:799-808, `PREV` empty — gate runs BEFORE the stop at :845) left launchd **LOADED+ARMED** on the broken binary, violating the doc's own rule ⇒ **ALL FOUR branches DISARM** (bootout ⇒ DARK + DISARMED + named journal state + loud operator message); per-branch states named; DISARM cell added | §5.4, §7, §12 |
| **B3** | The repo does `launcher_swap "$PREV"` **THEN** `atomic_flip` (promote.sh:850-851) and every release ships its own launcher (stage.sh:425-426) ⇒ the vehicle must **`launcher_swap "$LKG"` BEFORE rolling `current` back**, else launchd execs the NEW launcher against the LKG binary. Pin added | §5.3 step 1, §12 |
| **B4+B5** | ONE coherent classifier change: on Darwin **AUTO probes launchd first** (gui domain primary) and **REFUSES (rc 78)** on zero/multiple/unresolvable — never falls through to `SCRIPT_NOHUP` (today lib.sh:6045-6049 returns rc 0 for ANY non-Linux host before any detection); and `script`/opt-out on Darwin ⇒ **rc 78** unless the operator acknowledges an **unsupervised lane with a distinct declaration** (not `script`) — repo doctrine (lib.sh:6010-6016) says refuse, not degrade. §15's blessing of `script` on Darwin is **removed**. Two cells added | §6, §12, §15 |
| notes folded | exit-code overload → distinct vehicle codes; availability regression (DARK+DISARMED) in §4/§14; realpath symmetry (both sides resolved); Darwin refusal-rung wording made reachable; twins cell-list-vs-count pin; classifier blast radius on restart.sh/rollback.sh; TERM-suppression ownership (D1 implements, D2 specifies+pins); lost `_boot_grace_start` window; census seam named; §8 over-claims dropped; §15.1 re-stage = build+stage the release payload (`scripts/upgrade/stage.sh`), never `make install` on a live dir; mode declaration = **process env only** (no `.env` fallback); **Option-4 KeepAlive-semantics alternative row**; `journal_update` serialization mechanism named; anchor drift lib.sh `:6449→:6452` (map call site) | §3–§15, §19, §20 |
| demoted | Everything else (nit-level pins, e2e plumbing, inventory detail) → **§20 implementation-time notes (owned by the implementer)** — not re-litigated in the body | §20 |
| standing facts | **S11/S13 are STRUCK at approver closure** — this doc no longer claims they are carried; any requirement they once held is re-commissioned separately. **B-B4 (supervisor-absent window availability regression) is a Note, not a blocker** | §13, §14, §16, §18 |

---

## 1. Problem

At the v0.29.16 flip (2026-11-11 10:45–46 +07) two restarters raced: launchd KeepAlive restarted **its** launcher (exit 143) while promote started its own detached launcher (portless). The launchd tree won `:9797`; the version-verify gate (`gate_version`, lib.sh:6500-6511) correctly read 0.29.8 ≠ 0.29.16 ⇒ halt-for-human with journal `current=v0.29.16` and v0.29.8 serving, KeepAlive still armed. The stale relaunch booted WITH the journal ⇒ ADR-012 wedge ⇒ ~16 min FE-dead.

**Flat-binary condition (complete):** `resolve_binary` (launcher.sh:3653-3670) **prefers** `current/ensemble-prod` and reaches the flat `INSTALL_DIR/ensemble-prod` only when `current/ensemble-prod` is absent/non-executable ⇒ the old-binary start required the flat binary to exist **and** be executable **and** `current/ensemble-prod` to be absent/non-executable at that moment.

## 2. Root cause (verified)

- `lib.sh:6045-6049` — **any** non-Linux host returns `SCRIPT_NOHUP` + rc 0 **before any detection**: the AUTO path never probes launchd on Darwin.
- `lib.sh:5978-5985` — the `0|false|no|off` and `script` arms return rc 0 unconditionally; `lib.sh:6010-6016` — repo doctrine: a declaration the host cannot honor must **refuse (78), never loud-degrade at rc 0** ("the r-f82e surprise class").
- No `launchd` arm existed at all ⇒ a launchd-armed Mac classified as script ⇒ nohup hand-back ⇒ the 2026-11-11 incident, reported `conforming`.
- `lib.sh:6343-6344` — dual-fire detector is Linux-only; the gap was documented-but-uncommissioned (`docs/runbooks/systemd-adoption.md` A2 row 5: service×macOS = FUTURE scope).

## 3. Recommendation and alternatives

**Option-1 Adoption** (supervisor owns start; no stop barrier) · **Option-2 Deterministic** (barrier first, then supervisor-owned start) · **Option-3 Verify-retry** (keep racing, re-verify) · **Option-4 KeepAlive-semantics change** (e.g. add a `SuccessExitStatus=143` analog so a graceful stop does not re-arm the job — GAP-2, docs/deployment.md:328; the deploy doc lists no analog today).

**Sub-mechanisms:** **M1 deterministic barrier** (`launchctl bootout` before any signal) · **M2 supervisor-owned start** (`launchctl bootstrap` exactly once) · **M3 bounded re-verify** (verify-read retries only).

> **Recommendation: Option-2 core = M1 + M2.** M3 survives only as bounded post-verify recovery.
> **Option-4 is rejected for this PR:** it requires a **plist change** (operator-side, out of scope here) and cannot suppress the flip-window race — the job can re-arm mid-window. It remains the durable long-term complement (note: launchd's `SuccessfulExit=false`/exit-143 re-arm is exactly why bootout, not a bare TERM, is the stop primitive — docs/deployment.md:328/:370-372).

> **Invariant (rescoped — no post-bootout over-claim):** *No launcher may be exec'd by anyone between `bootout` and `bootstrap`; a straggler may exist while the ownership ladder completes after bootout; from the ladder's verified-clean point — which precedes the flip — through bootstrap, zero `ensemble-prod` processes exist.*

## 4. Five-axis trade-off matrix

| Axis | Option-1 | Option-2 | Option-3 | Option-4 | **Option-2 core (M1+M2) — REC** |
|---|---|---|---|---|---|
| **Complexity** | Med — collapses into M1 | Med | Low — retry loop + hidden debt | Low | **Med** |
| **Scalability** | Low-Med | High | Low | High | **Highest** |
| **Maintainability** | Low-Med | High | Low | Med | **High** |
| **Risk** | Med | Med-Low | **High — incident × N** | Med (re-arm persists in-window) | **Lowest among the options; Med in absolute terms while 🔴 residuals are open** |
| **Cost** | Lowest | Med | Low-Med | Low + operator plist change | **Med — one-time** |

**Availability cost of the recommended design (promoted):** a supervised abort leaves the daemon **DARK + the supervisor DISARMED** (no self-revival) — an **availability regression by design** (fail-closed): uptime is traded for determinism, and recovery becomes operator-driven (§14).

## 5. Choreography

### 5.1 Forward promote (supervised)

1. **Preflight** — classify (§6) ⇒ `LAUNCHD_MANAGED:<label>`; from the same per-job print: program predicate (argv[0] `/bin/bash` ∧ **resolved** argv[1] == **resolved** `$INSTALL_DIR/launcher.sh` — both sides resolved, so a symlinked install matches; unresolvable ⇒ rc 78); effective `ExitTimeOut`; **verbatim `path =` plist path** (label ≠ filename); pre-stop pid. **Lineage:** `ps -o ppid=` chain from the port-holder up — legitimate iff the chain passes through the job's pid before pid 1, corroborated by a pgid match; unresolvable ⇒ rc 78. No `launchctl print` forks/parent-pid substrate exists (in-repo usage parses state/pid only — `agents/gaia/workflow.md:189`; the inspiration parser reads exactly four keys — `state`, `pid`, `last exit status`, `last exit reason` — and notes the output "is not a stable API", `.inspiration-projects/openclaw/src/daemon/launchd.ts:247-273`, :509). **The census (step 5) is the hard gate; lineage corroborates.**
2. **★ BOOTOUT (M1).** `launchctl bootout gui/$UID/<label>`, bounded ~30 s via `_run_bounded` (lib.sh:4611); domain-absence verified (bounded ~5 s). Still loaded after a real error ⇒ rc 78 ("not found" = benign-idempotent). **Bootout DOES TERM the job** — launchd escalates to SIGKILL after the job's `ExitTimeOut`; the launcher forwards that TERM to the daemon (launcher.sh:3683-3690, no once-guard) ⇒ see step 3.
3. **Straggler stop — TERM-IDEMPOTENT.** Ownership-scoped ladder using the **census seam = `stop-ensemble.sh` `_list_candidates()` (:477; Tier 1a :478, Tier 1b :483) + `_classify()` (:510; Tier 1a :516, Tier 1b :526)** — the single source, covering absolute-anchored, **relative-argv/cwd-anchored** (`lib.sh:5688` produces `nohup ./launcher.sh` with cwd=INSTALL_DIR), `current/ensemble-prod` and PHYS_DIR forms (`stop-ensemble.sh:528-531`). **After bootout the ladder sends NO second TERM to an already-terminating daemon**: liveness/port check first; SIGKILL only if still alive after the drain budget (§8). **Ownership:** the *contract* is D2's; the *implementation* is D1's (stop-ladder internals) — D2 states the requirement and pins it.
4. **Flip** — zero ensemble processes in this window.
5. **★ Census gate, then BOOTSTRAP (M2).** Census failure ⇒ vehicle (§5.3). Gate passed ⇒ `launchctl bootstrap gui/$UID <verbatim-plist-path>`; `RunAtLoad=true` starts the job. "Already loaded" benign **only after revalidation**. **The supervisor-boot path has no `_boot_grace_start` window** (that helper is invoked only on the nohup hand-back — lib.sh:4437 / :5696) ⇒ the verify path carries its own bounded wait.
6. **Verify — ownership, then version.** Job running; pid ≠ pre-stop pid; the port holder must be INSTALL_DIR-anchored (**this is the dual-fire/census gate — there is no separate "provenance check" mechanism; the build sidecar lib.sh:3245-3274 and restart-lane gate lib.sh:1457-1471 are not identity checks**); then `livez` → `readyz` → `gate_version` (lib.sh:6500-6511) → soak; bounded verify-read retry (2 × ~5 s).
7. **Commit (8a) or auto-rollback (8b)** ⇒ §5.4 (8b is **not** the vehicle).
8. **In-window aborts** ⇒ §5.3.

### 5.2 Unsupervised lane

The hand-back path is unchanged **where it is legitimate**: non-Darwin hosts, and Darwin hosts that acknowledge the unsupervised lane with the **distinct declaration `ENSEMBLE_SUPERVISION=unsupervised`** (§6). `script` on Darwin is **not** an acknowledgement (B5).

### 5.3 The abort vehicle — `_abort_restore_supervisor` (PRIMARY window only)

**Scope:** every exit path inside the promote's **primary** window (forward promote: bootout → bootstrap) routes through the vehicle. 8b opens a **second** window and uses the launchd-aware promote.sh path (§5.4). rollback.sh / restart.sh are out of scope (own lanes/pins — but they share the classifier, so the global change reaches them: §6 blast radius).

**Reproducible exit-site criterion:** raw `grep -c 'exit 78' scripts/upgrade/promote.sh` = **36**, `grep -c 'exit 1\b'` = **13** (both include comments) — 28 of the exit-78 occurrences are non-comment sites. The census is a **procedure**: a pin enumerates the in-window exit paths at implementation time and asserts each routes the vehicle.

**Reachability:** `_enforce_deadline` (def lib.sh:525; lock release `:561` → `exit 78` `:562`) and `_signal_handler` (:2992-3012; lock release `:3000`→`:3003` → exit `:3007-3010`).

**Lock-free by construction:** both entries release the lock before exiting ⇒ the vehicle runs lockless — idempotent, no lock acquisition; **pinned** (no lock-dir creation, no owner write).

**Sequence (ordered):**
0. **Loaded-state check FIRST:** already loaded ∧ healthy ⇒ validate-and-continue (no double bootstrap).
1. **★ Restore LKG — launcher FIRST, then the symlink (B3).** `launcher_swap "$LKG"` **BEFORE** rolling `current` back to LKG — mirroring the repo's own order (`launcher_swap "$PREV"` THEN `atomic_flip "$PREV"`, promote.sh:850-851). Every release ships its own launcher (stage.sh:425-426) ⇒ without the swap, launchd would exec the **new** launcher against the **LKG** binary — the exact skew §5.4 step 3 exists to prevent — and "LKG serving, armed" would be unachievable.
2. **Re-apply the census** (Tier-1a∪1b, §5.1 step 3). **Failed census ⇒ halt WITHOUT re-bootstrapping + terminal close:** disposition `aborted / rolled-back-to-LKG / supervisor-absent`, event **`abort_supervisor_absent`** (detail string carries the `supervisor=absent` token), exit **78** (halt class; env NOT recovered), with the explicit note that the repo's "1 = env recovered" semantics does **not** apply; output names the failed clause and does **not** claim "deterministic-down" (a straggler exists and may be serving).
**2a. ★ TERMINAL CLOSE — BEFORE RE-BOOTSTRAP: `journal_close_txn` ONLY.** `journal_close_txn` (lib.sh:2027-2029 = `journal_update "in_flight" "null"`) is lockless. **Serialization mechanism (promoted):** `journal_update` (lib.sh:1737) is a **single read → value splice → atomic `journal_write` replace** (the D1-16 discipline: one read, so the update cannot straddle a concurrent writer's atomic replace) — the terminal close therefore inherits that atomic-replace serialization without needing the pipeline lock. **The heartbeat alternative is deleted:** `lock_heartbeat` (lib.sh:2650-2656) needs the lock dir and `owner == $$` ⇒ **guaranteed no-op** in the lockless vehicle, and its branch violates the step's invariant — a fresh heartbeat with a dead owner still leaves the txn OPEN+flipped, and the launcher's sweep would then roll `current` past the restored LKG.
3. **Re-bootstrap the ORIGINAL job** — verbatim `path =` plist path; never label-derived. **TERM-idempotence:** the restore path must not deliver a second TERM to a still-terminating daemon (bootout already TERMed it; the launcher forwards every signal — launcher.sh:3683-3690, `CHILD_STOP_WAIT_S=70` at :60).
4. **Verify pid alive → branch:**
   - alive ⇒ disposition `aborted / rolled-back-to-LKG / supervisor-restored`, event **`abort_supervisor_restored`**, exit **1** (the repo's "rolled back, env recovered, promote failed" semantic — promote.sh:58-61).
   - **not alive ⇒ neither branch applies** ⇒ **third disposition** `aborted / rolled-back-to-LKG / supervisor-unverified`, event **`abort_supervisor_unverified`**, exit **78** (halt class; supervisor booted-but-unverified, txn CLOSED, LKG restored, no pending repoint) — operator decides.
5. **Halt (exit-code contract, distinct):** the abort **reason** rides the journal event; the **exit code** is defined per branch — **1 = env recovered** (restored only), **78 = halt class** (absent / unverified), and **signal-triggered aborts keep the repo's attribution** (143 TERM / 129 HUP / 130 INT). No blanket 1; exactly one of the three events per run.

**Case 2 — the vehicle could NOT run (SIGKILL inside the window). TRUE end state (B1):** the promote refreshes the txn heartbeat every loop (`lock_heartbeat` → `journal_heartbeat`, lib.sh:2650-2663) ⇒ at SIGKILL the heartbeat is ≤30 s old ⇒ **fresh** (`HEARTBEAT_STALE_S=300`, launcher.sh:97) ⇒ `hb_stale=0` ⇒ **comp3 does NOT fire** (it requires `hb_stale=1 ∧ owner_dead=1`, launcher.sh:3317-3325) ⇒ the sweep takes the "leaving alone" branch (`return 0`) ⇒ **the launcher boots the FLIPPED, possibly-broken `current` SILENTLY — no halt-for-human, no operator alert from the sweep**. The txn stays OPEN until the heartbeat goes stale (>300 s) *and* the owner is dead — a later boot's comp3 may then reclaim — but the **most likely sub-case is a silent boot of the broken `current`**.
**Mitigation (ruled):** (i) the launcher's reclaim must treat **OWNER-DEAD as reclaimable even with a fresh heartbeat** (the owner pid is dead — directly checkable) with a **bounded wait**; (ii) the **watchdog alert** covers the residual gap (the watchdog is the `supervisor=absent` detector — a SIGKILLed promote journals nothing). Recovery path: watchdog alert → manual `bootstrap` → launcher sweep (with mitigation (i) reclaiming) → if `previous` is not `rollback_safe` ⇒ **HALT-FOR-HUMAN on the possibly-broken current** (launcher.sh:2700-2709) and the **next promote refuses rc 78** (pipeline-busy, `journal_open_txn` lib.sh:1951-1958) until the txn clears.

**Doctrine amendment (same PR):** `promote.sh:63-66` leave-OPEN doctrine scoped to case 2; case-1 aborts close terminally.

### 5.4 8b — the SECOND window (gate-first, ALL FOUR branches DISARM)

1. **Manifest/quarantine gate FIRST (before the stop at :845)** — four HALT-for-human branches. Each: close the txn, set `current` back to `$VERSION`, **and DISARM the supervisor** — `launchctl bootout` ⇒ **DARK + DISARMED**, a **named journal state**, and a **loud operator message** with the one-command recovery. Per-branch states:
   - `PREV` empty — no previous release (promote.sh:799-808) ⇒ state **`supervisor-disarmed-no-previous`**.
   - `PREV` quarantined (:810-817) ⇒ **`supervisor-disarmed-prev-quarantined`**.
   - `PREV` missing (:819-826) ⇒ **`supervisor-disarmed-prev-missing`**.
   - `PREV` not `rollback_safe` (:828-836) ⇒ **`supervisor-disarmed-prev-unsafe`**.
   *Why DISARM in all four:* the gate runs **before** the stop, so without an explicit bootout the supervisor would be left **LOADED + ARMED on the broken binary** — the exact state the doc's own rule forbids. (Restore-PREV is available only in the normal path below, where PREV is `rollback_safe`.)
2. **Stop the failed release** — launchd-aware `stop_via_stop_script` (bootout), TERM-idempotent. **Stop failure** (promote.sh:846-847) ⇒ HALT-FOR-HUMAN with the repo's semantics (*"txn left open — the next launcher start sweep-rolls-back to previous"*), exit 1, and the named end state **DARK + DISARMED + OPEN txn**.
3. **Restore PREV** — **`launcher_swap "$PREV"` (promote.sh:850)** then `atomic_flip "$PREV"` (:851). The swap restores the **root `INSTALL_DIR/launcher.sh`** that launchd's `ProgramArguments` points at (ensemble-prod.plist:100) — so the supervisor's next start execs the restored launcher, not a stale one.
4. **Restart via the supervisor** — `restart_via_launcher`'s launchd branch (bootstrap; never a promote-started launcher).
5. **Re-gate** — bounded verify; on failure, halt with the named end state (never live-wrong-and-armed).

## 6. Detection — classifier change (code requirements, B4/B5)

The Darwin behavior is **one coherent classifier change**, not a declared arm plus caveats:

- **B5(i) — the non-Linux short-circuit is removed for Darwin.** Today `lib.sh:6045-6049` returns `SCRIPT_NOHUP` + rc 0 for ANY non-Linux host *before any detection*. **New:** on Darwin the AUTO path **probes launchd first** (gui domain primary — the operator lane is a USER LaunchAgent by design: `make plist-install` → `$(HOME)/Library/LaunchAgents/ensemble-prod.plist`, `launchctl bootstrap gui/$(id -u)`, Makefile:409-424; docs/deployment.md:87-91 **"Never sudo the two plist targets"**; no `sudo` in `scripts/upgrade/*.sh`) and **REFUSES (rc 78)** on zero / multiple / unresolvable — it **never falls through to `SCRIPT_NOHUP`**.
- **B5(ii) — `script`/opt-out on Darwin refuses.** The `script` and `0|false|no|off` arms (lib.sh:5978-5985) return rc 0 unconditionally today. **New:** on Darwin they **refuse (rc 78)** unless the operator acknowledges an intentionally unsupervised lane with a **distinct declaration** — `ENSEMBLE_SUPERVISION=unsupervised` (a NEW value, not `script`).
- **B4 — a `launchd)` arm exists**, modelled on the `unit)` arm (lib.sh:5986-6034): `SUPERVISION_MODE=launchd`, `SUPERVISION_STATE=LAUNCHD_MANAGED:<label>`, label precedence `ENSEMBLE_LAUNCHD_LABEL` > auto-detected.
- **Refusal rungs — wording made REACHABLE (promoted):**
  - **A** — declared `launchd` on a **non-Darwin** host ⇒ rc 78, token `supervision-launchd-incapable-host` (mirrors the unit rung :6019-6023).
  - **B** — declared `launchd` on Darwin with **no resolvable job** (zero or multiple matches) ⇒ rc 78, token `supervision-not-verified`.
  - **C** — declared `script`/opt-out on Darwin ⇒ rc 78, token `supervision-unsupervised-declaration-required` — **unless** `unsupervised` is declared.
  - **D** — garbage/unknown declared value on Darwin ⇒ rc 78 (fail-closed; today: warn-once + rc 0 + mode=script ⇒ the incident mechanism).
- **The `system/` domain is optional/informational.** An unreadable/garbage/empty `system/` probe is **not** a refuse trigger when the gui probe yields exactly one match and (declared=auto OR the declared label resolves there).
- **Mode declaration placement (promoted):** `ENSEMBLE_SUPERVISION` and `ENSEMBLE_LAUNCHD_LABEL` are read from the **process environment only** — **no `INSTALL_DIR/.env` fallback** (a stale on-disk declaration must never silently change the supervision mode).
- **Token convention:** the **global** machine line carries the **unprefixed** state token (`ENSEMBLE_SUPERVISION_RESULT=LAUNCHD_NOT_VERIFIED`, mirroring lib.sh:6022); the **journal/refusal** path carries the **prefixed** form (`reason=supervision-not-verified`, mirroring lib.sh:6447; pinned at tests/test_supervision_stop_handback.sh:939).
- **Program predicate (promoted, symmetric):** compare **resolved** paths on **both** sides — `realpath(argv[1])` == `realpath("$INSTALL_DIR/launcher.sh")`; a symlinked install then matches by construction; if either side fails to resolve ⇒ rc 78.
- **Global-classifier blast radius (promoted):** restart.sh and rollback.sh share this classifier ⇒ on Darwin, hosts without an explicit `unsupervised` acknowledgement will **refuse restart and rollback as well** (not only promote). This is intended (fail-closed) and must be stated in the runbook.
- **Cost (S15):** ~410 jobs in the live dump ⇒ dump-parse narrowing; every launchctl call via `_run_bounded` (lib.sh:4611). **S4/R23:** `LAUNCHCTL_BIN` default mirrors `SYSTEMCTL_BIN`'s deliberately **PATH-resolved** default (lib.sh:5043-5049) so the comp7 stub seam works; env override remains for absolute pinning.
- **Host predicate (R14):** host-awareness lives in a **shared host predicate mirrored in both twins** — never inside the outcome map (3-arg pure; 19 cells byte-identical: tests/unit/tools/test_supervision_python.py:187-224; tests/test_supervision_twins.sh:14/:100).

## 7. Failure + rollback matrix

| Step / failure | Response | Named end state |
|---|---|---|
| Non-Darwin unsupervised | Script path | Unsupervised unchanged |
| **Darwin auto + zero/multiple match (B5)** | **rc 78** — never SCRIPT_NOHUP | Loud, safe |
| **Darwin declared `script`/opt-out (B5)** | **rc 78** unless `unsupervised` declared | Loud, safe |
| **Garbage declared value on Darwin (B4)** | **rc 78** (was rc 0 + script) | Loud, safe |
| Declared `launchd`, non-Darwin host | rc 78 `supervision-launchd-incapable-host` | Loud, safe |
| `system/` probe unreadable, gui = 1 match | Informational only — **no refusal** | Proceed |
| Preflight mismatch / lineage unresolvable / foreign job | rc 78 | No change |
| Bootout fails / still loaded | rc 78 pre-mutation; "not found" benign | No change |
| Straggler stop fails | Ladder ⇒ census-gated vehicle | restored / absent / unverified |
| Flip fails | Vehicle: LKG swap+rollback, terminal close, restore | LKG serving, armed |
| **Census fails** | RC-1 contract: `abort_supervisor_absent`, exit 78, failed clause named, **no "deterministic-down"** | Straggler-dependent, truthfully reported |
| **Bootstrap fails (rc≠0)** | Retry N=2 (2s→4s); exhaustion ⇒ vehicle | restored / absent / unverified |
| **8b: PREV empty (B2)** | **DISARM** ⇒ DARK + DISARMED + `supervisor-disarmed-no-previous` + loud message | Dark, disarmed, operator-driven |
| **8b: PREV quarantined** | DISARM ⇒ `supervisor-disarmed-prev-quarantined` | Dark, disarmed |
| **8b: PREV missing** | DISARM ⇒ `supervisor-disarmed-prev-missing` | Dark, disarmed |
| **8b: PREV not rollback_safe** | DISARM ⇒ `supervisor-disarmed-prev-unsafe` | Dark, disarmed |
| **8b: stop failure** (:846-847) | Halt; txn left OPEN | **DARK + DISARMED + OPEN txn**, exit 1 |
| **8b: normal path** | `launcher_swap "$PREV"` + `atomic_flip` + bootstrap + re-gate | PREV serving, armed |
| Verify mismatch / timeout | 8b (promote.sh path) — vehicle unreachable post-bootstrap | named states above |
| **Re-bootstrap attempted, pid NOT alive** | **`abort_supervisor_unverified`**, exit 78 | Bootstrapped-but-unverified; operator decides |
| **Promote SIGKILLed in-window (B1)** | Sweep leaves the txn alone (fresh heartbeat ⇒ comp3 does NOT fire) ⇒ **the flipped, possibly-broken `current` boots SILENTLY**; mitigation: owner-dead reclaim + watchdog alert; recovery → manual bootstrap → reclaim, or halt-for-human if `rollback_safe:false`, then next promote refuses 78 until the txn clears | See §5.3 case 2 |
| Parse garbage from `launchctl print` | Fail-closed; real-fixture pinned | Loud, safe |

## 8. Graceful drain (no over-claims)

- No plist change in this PR (payload carries no plist, `stage.sh:390-426`; operator-side only, §15.1).
- Effective `ExitTimeOut` read from the per-job print at runtime; **key absent ⇒ Apple's documented default ≈20 s** (the live 5 s is the *job's* value, not launchd's default), **LOG the assumption**.
- Budget = min(ExitTimeOut, WAIT_S); WAIT_S = knob `DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` (live 60 s) + 10 s margin = 70 s (mirrors `CHILD_STOP_WAIT_S=70`, launcher.sh:60).
- **Measured facts only:** docs/deployment.md:383 measures **"graceful drain ~1 s"** (re-observed ×4: TERM → exit 143 in 1–2 s; the 70 s budget never engaged — docs/deployment.md:330) ⇒ **normal drain ≈1–2 s; WAIT_S = 70 s is the upper bound**. The incident's daemon ignored TERM ~70 s (pathological) ⇒ SIGKILL ⇒ voided checkpoint. The live 5 s `ExitTimeOut` truncates even the normal path's headroom.
- **🔴 Activation gate:** a graceful checkpoint write is voided on **every** supervised promote under the live 5 s cap ⇒ the "fully automatic next promote" is gated on §15.1 (operator steps NOT yet committed).

## 9. Grammar + outcome cells

Helpers in `scripts/upgrade/lib.sh` wrapping the existing seams: `_supervision_host_allows_launchd()`, `_supervision_launchd_detect()`, `_supervision_launchd_bootout()`, `_supervision_launchd_bootstrap()`, `_abort_restore_supervisor()`; `LAUNCHCTL_BIN` (PATH-resolved default + env override).

- `stop_via_stop_script` gains a launchd branch **before** the UNIT branch; `restart_via_launcher` gains one **before** the nohup fallback (`lib.sh:5688`) — also 8b's launchd-awareness.
- **Outcome grammar:** the additive `ENSEMBLE_SUPERVISION_OUTCOME=` line (emitted **lib.sh:6453**; comment :6431-6433; after the DUAL_FIGHT check). Frozen `ENSEMBLE_SUPERVISION_RESULT` grammar untouched — declaration **lib.sh:5778-5781** (rows :5790-5805) ↔ `daemon/tools/upgrade_journal.py:3628`; restated :6434.
- **Map signature (PN-1):** `supervision_map_outcome <declared> <verified> [<unit>]` — def **lib.sh:6207-6208**, ladder **:6216-6254**, **call site lib.sh:6452** (drift corrected from :6449). The **Verified** column carries the BARE state; the labeled form belongs in **Unit**; passing it as `verified` misses every arm ⇒ fault default. The map receives the **RESOLVED** mode (:6452); the resolved-mode equivalence note (:5802-5806) demands row-for-row agreement; the Darwin refusals fire **before** the map call (:6442-6451) ⇒ **the map stays pure and host-blind** and the Darwin outcome is produced by the classifier refusal.
- **Two layers:** (a) the pure map cells — `launchd`×`LAUNCHD_MANAGED`(label)/`conforming|launchd topology declared and verified, label resolved (LAUNCHD MODE)`; `launchd`×`LAUNCHD_MANAGED`(no label)/`fault|launchd declared but no label resolvable — preflight refuses (exit 78), never silent-degrade`; `launchd`×`SCRIPT_NOHUP`/`fault|declaration mismatch: launchd declared but script reality — never silent-degrade`; `launchd`×`SCOPE_SURVIVOR`/`fault|declaration mismatch: launchd declared but scope-survivor reality — never silent-degrade`; `auto`×`LAUNCHD_MANAGED`/`conforming|auto defers to verification: launchd topology confirmed (D2 adoption signal)`; `script`×`LAUNCHD_MANAGED`/`degraded|declaration mismatch: script declared but launchd-managed reality — latent two-masters hazard`; unknown → the existing default `fault|unknown declared mode or verified state — fail-closed`. (b) the **classifier-level** Darwin outcome: rc 78, global token `LAUNCHD_NOT_VERIFIED`, journal token `supervision-not-verified`, outcome **fault** — produced by the refusal path, not by a host-aware map row.
- **PN-3:** the in-flight stamp is the **triple `supervision`/`unit`/`outcome`** (fn `journal_mark_supervision` **lib.sh:6467**; write **:6490**; `gate_livez` **:6495**; `gate_version` **:6500-6511**) — **no `supervisor=` key exists and none may be minted**; the `supervisor=absent`/`restored`/`unverified` tokens ride the **event detail string**.
- **PN-4:** register `abort_supervisor_restored` / `abort_supervisor_absent` / `abort_supervisor_unverified` in `_OUTCOME_LABELS` (`daemon/tools/upgrade_tools.py:1255`, region :1304-1317; status-lane fallback at :2592/:2597) — and **they must NOT enter the frozen `_TERMINAL_EVENTS` tuple** (`daemon/tools/upgrade_journal.py:2088-2144`): their home is the event detail string; the executor's exit closes the pending op.
- **Lane split:** D1 = stop-ensemble.sh internals (incl. the TERM-suppression implementation); D2 = lib.sh branches/helpers; D3 = restart.sh; `rollback.sh` = lock-holding sibling rider.

## 10. systemd parity

Structural mirroring at the same helper sites (bootout ↔ `systemctl stop` suppressing `Restart=`; bootstrap+new-pid ↔ `systemctl start`+`NEW-MainPID`; the no-fallback posture inherited). `scripts/systemd/ensemble-daemon.service` + `adopt-unit.sh` untouched.

## 11. Verify + journal interaction

- **No standalone "provenance check"** — no repo mechanism performs a daemon-identity check (build sidecar lib.sh:3245-3274; restart-lane gate lib.sh:1457-1471 — neither is one). Identity = the **dual-fire/census ownership gate**, then the version gate.
- Between flip and verify only the supervisor's fresh job can exist (pid ≠ pre-stop).
- **Journal:** no new keys (triple stays `supervision`/`unit`/`outcome`, lib.sh:6490); vehicle halts carry one of the three events with disposition + `supervisor=…` tokens in the **detail string**; case-1 aborts close the txn terminally **before** re-bootstrap; outcome via the additive OUTCOME line; **serialization = `journal_update`'s single-read + splice + atomic `journal_write` replace** (lib.sh:1737).

## 12. Test strategy

**New pins:**
1. **Ordering pin** — `bootout < TERM < flip < bootstrap`; zero launcher execs by promote in-window. **Asserts on stub call logs / exec traces, not text bans** (text bans secondary).
2. **Vehicle enumeration + order** — implementation-time census (raw pair 36/13, 28 non-comment exit-78 sites) ⇒ every in-window exit path routes the vehicle; order = loaded-state-first → **launcher_swap LKG → rollback** → census → **`journal_close_txn` before re-bootstrap** → verbatim re-bootstrap → pid-alive → three-way branch → halt with the branch's exit code. Cells: restored ⇒ exit 1 + `abort_supervisor_restored`; census-fail ⇒ exit 78 + `abort_supervisor_absent` + caveat + clause-named + no-deterministic-down; pid-not-alive ⇒ exit 78 + `abort_supervisor_unverified`; **exactly one event per run**; 8b/verify-timeout asserted NOT vehicle-routed. **NEW: "lock-free by construction" pin** (no lock-dir creation / owner write). **NEW: launcher-sweep cell** — the vehicle's abort disposition survives a sweep against an OPEN+flipped txn (no repoint past the restored LKG). **NEW: abort END-STATE pin** (txn closed, `current`==LKG, root launcher == LKG release's launcher (B3), job running-or-absent per branch).
3. **B3 pin** — after the vehicle's step 1, the **root `INSTALL_DIR/launcher.sh` content equals the LKG release's launcher** (stage.sh:425-426 ships a launcher per release) and `current` == LKG.
4. **TERM-idempotence pin** — after bootout the ladder sends **no second TERM** to a terminating daemon; SIGKILL only if still alive. A double-TERM reaching the daemon = checkpoint-void regression ⇒ failing cell.
5. **Byte-identity / unsupervised** — zero STATE-CHANGING launchctl calls on the hand-back path (call-log assert); goldens (Linux/absent + Darwin-with-stub); read-only-detection pin.
6. **Detection cells (B4/B5)** — gui hit; system-domain informational; **auto + zero match on Darwin ⇒ rc 78**; **declared `script` on Darwin ⇒ rc 78 unless `unsupervised` acknowledged**; **garbage declared value on Darwin ⇒ rc 78**; declared `launchd` on non-Darwin ⇒ rc 78 `supervision-launchd-incapable-host`; declared `launchd` + unresolvable label ⇒ rc 78 `supervision-not-verified`; gui zero-or-multiple ⇒ rc 78; both token forms (global `LAUNCHD_NOT_VERIFIED`, journal `supervision-not-verified`); mode read from **process env only** (a `.env`-present value must NOT take effect); parse-garbage fail-closed; Darwin zero-`/proc` cells.
7. **Lineage** — in-tree holder passes (ps-ppid chain through job pid + pgid); orphan/out-of-tree refuses; unresolvable refuses; **negative cell: ppid==1 without chain-through-job-pid ≠ legitimate**. Pinned against a **captured real (redacted) `launchctl print` fixture** — a synthetic stub is vacuous.
8. **Outcome cells** — byte-identical across twins, bare-verified + unit-label invocation, happy path `conforming`, fault defaults, classifier-level Darwin fault row (map stays host-blind ⇒ the 19-cell purity pins stay valid).
9. **Twins table** — launchd cells added; **the header count must equal the cell-list length (both twins)** — the pin is count == list length, not a literal number; 🟢 launchd arm ⇒ NO `launcher started (nohup` line (call-log assert).
10. **Drain** — budget = min(runtime ExitTimeOut, knob); absent-key fallback ≈20 s + logged assumption; `stop_forced_kill` stamp.
11. **Census cells** — relative-argv/cwd launcher (`nohup ./launcher.sh`, cwd=INSTALL_DIR) ⇒ FAILS; `current/ensemble-prod` form ⇒ FAILS; foreign install ⇒ PASSES; wedge-guard pid census in-window.
12. **8b DISARM cells (B2)** — **all four** PREV branches ⇒ bootout executed ⇒ DARK + DISARMED + the named per-branch journal state + the loud operator message (and **no** nohup hand-back); **including the fourth branch (`PREV` empty, promote.sh:799-808)**; plus the stop-failure cell ⇒ DARK + DISARMED + OPEN txn (exit 1); plus the normal path ⇒ `launcher_swap "$PREV"` + `atomic_flip` + bootstrap + re-gate.
13. **NEW Darwin e2e suite — MERGE GATE (distinct label + cleanup + opt-in real-seat leg):** the twins ladder is fenced off on non-systemd hosts, so it cannot gate this change; the suite asserts one launcher start, pid new, version == target, journal committed, OUTCOME `conforming`, no nohup line, abort end-state leg.

**Same-PR amendments to existing expectations:** **C10** — explicit-unit lineage: no `launcher started (nohup)`; refusal journaled with `reason=supervision-unit-incapable-host` (pinned, tests/test_supervision_stop_handback.sh:939); the launchd counterpart cells join it. **C10b — expectation CHANGE:** auto-resolved on an incapable host was **rc 0 + "launcher started (nohup)"** ⇒ on **Darwin** it becomes **rc 78**; the legacy degrade survives **non-Darwin** only.

**Must stay green:** `tests/test_launcher.sh`; watchdog suites; byte-identity spine; stop-ownership suite; `test_supervision_classify.sh`; `test_supervision_stop_handback.sh`; `test_supervision_e2e.sh` (fence untouched); `test_supervision_twins.sh`; `test_adopt_unit.sh`; `test_atomic_flip.sh`; `test_release_journal.sh`; `test_upgrade_overhaul_all.sh`.

## 13. Non-goals

- No launcher exit-code / KeepAlive contract changes (ADR-010/011) — Option-4 (KeepAlive-semantics) is a separate, operator-side plist change.
- **No plist change in this PR**; **no new journal keys**; **no `_TERMINAL_EVENTS` entries**.
- **No vehicle reachability from 8b/verify-timeout.**
- No python-daemon-sweep reliance for boot-path clearing.
- **No daemon-identity "provenance check" mechanism.**
- **No launcher-side signal once-guard in this PR** (the ladder-side TERM-suppression is the in-scope fix).
- No launchd adoption tool; no auto-adoption; no systemd lane change; no watchdog role change; no D1 process-group fixes; no unsupervised WAIT_S change; no ADR-012 cure.
- **S11/S13 are STRUCK** — this doc does not carry them; any requirement they once held is re-commissioned separately.

## 14. Residual risks

- 🔴 **Truncated drain / activation gate (§8)** — a graceful checkpoint write is voided on every supervised promote under the live 5 s cap until §15.1 lands.
- 🔴 **Availability regression (promoted):** a supervised abort leaves the daemon **DARK + the supervisor DISARMED** — no self-revival; recovery is operator-driven. By design (fail-closed), and it must appear in the operator runbook.
- 🟡 **Supervisor-absent window (B-B4 — a Note, not a blocker):** a SIGKILL inside the window can leave the **flipped, possibly-broken `current` booting silently** (fresh heartbeat ⇒ comp3 does not fire — §5.3 case 2). Mitigation: owner-dead reclaim + watchdog alert.
- 🔴 **Twins arm load-bearing** — same-PR Darwin surface or the twins contract breaks.
- 🟡 `launchctl print` grammar drift — real-fixture pinned, fail-closed, PATH-resolved `LAUNCHCTL_BIN` with env override.
- 🟡 Census-fail end state is straggler-dependent — clause-named halt output is the mitigation.
- 🟡 Flat root-path binary — the *race* is neutralised by the barrier and a flat-binary start is caught loudly by the version gate → 8b; retirement is an operator action (§15.4).

## 15. Activation gate + operator notes

**The "fully automatic next promote" depends on 1–2; the operator has NOT committed to them.**

1. **Raise the plist `ExitTimeOut`** (the template has NO key today): edit `scripts/ensemble-prod.plist` (add `<key>ExitTimeOut</key><integer>N</integer>`, N ≥ 70) → **re-stage the substituted plist into the live install** (`$(INSTALL_DIR)/data/ensemble-prod.plist`, produced by the install-time substitution at Makefile:371-372 — **NOT `make install` on a live dir**, which is forbidden) → `make plist-install` (Makefile:409-424 → `$(HOME)/Library/LaunchAgents/ensemble-prod.plist`) → reload (bootout + bootstrap). **The release payload itself is staged separately by `scripts/upgrade/stage.sh`** (no plist inside a release payload — `stage.sh:390-426`); the plist is an operator-side artifact. None of this ships in this PR.
2. **Declare supervision (process environment only — no `.env` fallback):** operator machine `ENSEMBLE_SUPERVISION=launchd` (+ optional `ENSEMBLE_LAUNCHD_LABEL`); **unsupervised Darwin hosts `ENSEMBLE_SUPERVISION=unsupervised`** — the distinct acknowledgement (B5). `script` on Darwin now **refuses**. Add the new values to `.env.example` (PN-5) as documentation of the vocabulary.
3. **Agent-domain/privilege fact:** the operator lane is a **USER LaunchAgent by design** — `$(HOME)/Library/LaunchAgents`, `launchctl bootstrap gui/$(id -u)`, **never sudo** (docs/deployment.md:87-91; no sudo in `scripts/upgrade/*.sh`). The lane stays user-level; `system/` is informational only.
4. **Retire the flat root-path binary** (conditional port-winning hazard — §1).
5. **Runbook:** SIGKILL-mid-window recovery = watchdog alert → manual `bootstrap` → launcher reclaim; **if `rollback_safe:false` → halt-for-human on a possibly-broken current** (launcher.sh:2700-2709); **next promote refuses rc 78 until the txn clears** (lib.sh:1951-1958); supervised aborts leave DARK + DISARMED (operator-driven recovery).

## 16. Implementation checklist (bounded by this doc)

**This pass:** B1 comp3 truth + owner-dead reclaim + watchdog mitigation · B2 all four 8b branches DISARM + per-branch states + cell · B3 `launcher_swap "$LKG"` before the vehicle's rollback + pin · B4/B5 one coherent classifier change (Darwin AUTO probes launchd; `script`/opt-out refuse; distinct `unsupervised` acknowledgement; reachable refusal rungs A–D) + two cells · folded notes: exit-code contract (1=recovered / 78=halt / 143-129-130 signals), availability regression in §4/§14, realpath symmetry, twins count==list length, classifier blast radius on restart.sh/rollback.sh, TERM-suppression ownership (D1 implements), `_boot_grace_start` window absent on the supervisor-boot path, census seam named (`stop-ensemble.sh` :477/:510), §8 over-claims dropped, §15.1 re-stage meaning, process-env-only mode declaration, Option-4 KeepAlive row, `journal_update` serialization, anchor drift :6449→:6452.

**Standing:** S2 traps→vehicle · S3 already-loaded revalidation · S4 LAUNCHCTL_BIN · S5 twin stub seam · S8 two-step detection · S9 declared mode + same-PR twin · S10 unpinned-cell inventory · S12 Darwin e2e merge-gate suite · S14 P2-consumer merge contract (`stop-ensemble.sh:675-678`) · S15 bounded detection cost · S16 anchor corrections · S17 path-anchored ownership. **(S11/S13 STRUCK — not carried.)** G1–G3 · RC-1 · PN-1–PN-5. C1 watchdog-as-detector.

## 17. Decisions pending (leader/operator)

1. **Activation-gate commitment (§15.1–15.2)** — the `ExitTimeOut` raise + the supervision declarations (incl. the `unsupervised` acknowledgement for any intentionally unsupervised Darwin host).
2. **Agent-domain posture** — confirm the lane stays user-level (gui primary, `system/` informational, no sudo); a system-domain lane would be a separate design.
3. Flat binary retirement (§15.4). 4. `ENSEMBLE_LAUNCHD_DISABLE_WINDOW` — deferred.

## 18. Open questions

- **Nothing is claimed closed on the demoted set** — §20 items are implementer-owned and explicitly not adjudicated here.
- `RunAtLoad`/bootstrap==start remains the single architecture flip-assumption (contingency in the header).
- The leader's ruling that a 4th iteration was authorized supersedes the approver's escalation cap; cycle-003 closure is the next gate.

## 19. Anchor index (kept current)

| Seam | Supports |
|---|---|
| lib.sh:525/:561-562 | `_enforce_deadline` + lock-release→exit-78 (vehicle reachability; lock-free) |
| lib.sh:2992-3012 | `_signal_handler` (:3000→:3003→:3007-3010) — signal-attribution exits |
| lib.sh:1737 | `journal_update` = single read → splice → atomic replace (terminal-close serialization) |
| lib.sh:2027-2029 | `journal_close_txn` — lockless, the ONLY step-2a option |
| lib.sh:2650-2666 | `lock_heartbeat` (lock-dir + owner==$$ ⇒ no-op in the vehicle) + `journal_heartbeat` refresh cadence |
| lib.sh:4437 / :5696 | `_boot_grace_start` — invoked only on the nohup hand-back (absent on the supervisor-boot path) |
| lib.sh:4611 | `_run_bounded` |
| lib.sh:5043-5049 | `SYSTEMCTL_BIN` PATH-resolved default (pattern for `LAUNCHCTL_BIN`) |
| lib.sh:5064, :6045-6049 | host-capability gate; the non-Linux short-circuit B5(i) removes |
| lib.sh:5778-5781 (rows :5790-5805) | vocabulary DECLARATION (frozen) |
| lib.sh:5802-5806 | resolved-mode equivalence |
| lib.sh:5978-5985 / :5986-6034 / :6010-6016 / :6019-6023 | opt-out+script arms (B5(ii) changes); unit arm (model for launchd arm); refuse doctrine; unit refusal rung |
| lib.sh:6045-6049 | non-Linux auto short-circuit (see above) |
| lib.sh:6207-6208 (def) / :6216-6254 (ladder) / :6452 (call) | outcome map signature + cells + resolved-mode call site |
| lib.sh:6343-6344 | dual-fire detector Linux-only |
| lib.sh:6431-6433 / :6453 | OUTCOME comment + emission site |
| lib.sh:6442-6451 | preflight refusal path (prefixed tokens) — Darwin refusals fire BEFORE the map call |
| lib.sh:6467 / :6490 / :6495 / :6500-6511 | `journal_mark_supervision` / triple write / `gate_livez` / `gate_version` |
| lib.sh:1951-1958 | `journal_open_txn` pipeline-busy ⇒ next promote refuses 78 |
| lib.sh:5688 | the ONLY nohup hand-back site (relative-argv `./launcher.sh`, cwd=INSTALL_DIR) |
| lib.sh:3245-3274, :1457-1471 | build sidecar / restart-lane gate — NOT identity checks |
| launcher.sh:60 / :78 / :97 | `CHILD_STOP_WAIT_S=70` / `SWEEP_STALE_S=600` / `HEARTBEAT_STALE_S=300` |
| launcher.sh:2700-2709 | sweep halt-for-human when `rollback_safe≠true` |
| launcher.sh:2884-2896 | leave-OPEN conjunct (owner-alive ∧ heartbeat-fresh) |
| launcher.sh:3317-3325 | **comp3 preconditions** (hb_stale ∧ owner_dead) + the "leaving alone" branch |
| launcher.sh:3653-3670 | `resolve_binary` prefers `current/ensemble-prod` |
| launcher.sh:3683-3690 | launcher forwards EVERY signal to the child (no once-guard) |
| launcher.sh:3941-3942, :537-560 | launcher bash boot sweep |
| promote.sh:58-61 / :63-66 | exit contract 0/1/78/75 / leave-OPEN doctrine |
| promote.sh:799-808 / :810-817 / :819-826 / :828-836 | the FOUR 8b PREV gates (all DISARM per B2) |
| promote.sh:845-847 / :850-851 | 8b stop (:845), stop-failure (:846-847), `launcher_swap` THEN `atomic_flip` |
| stop-ensemble.sh:477-483 / :510-531 | **census seam**: `_list_candidates` (Tier 1a/1b) + `_classify` (Tier 1a/1b) |
| stop-ensemble.sh:504 / :675-678 / :748-768 | path-anchored ownership / P2 consumer / ownership loop |
| ensemble-prod.plist:57 / :100 / :109 | bootout semantics / ProgramArguments→launcher.sh / RunAtLoad |
| Makefile:371-372 / :409-424 | plist substitution into the live install / `make plist-install` → `$(HOME)/Library/LaunchAgents` |
| stage.sh:390-426 / :425-426 | no plist in the payload / **every release ships its own launcher** |
| docs/deployment.md:87-91 / :118 / :328 / :370-372 / :383 | never sudo / kickstart / GAP-2 exit-143 re-arm / restart primitives / drain ≈1 s |
| upgrade_journal.py:3628 / :2088-2144 / :3789-3791 / :3893 | vocabulary twin / frozen `_TERMINAL_EVENTS` / Darwin short-circuit / pure `supervision_outcome` |
| upgrade_tools.py:1255 / :1304-1317 / :2592/:2597 | `_OUTCOME_LABELS` + status-lane fallback |
| tests: supervision_python.py:187-224 · twins.sh:14/:100/:102-122 · stop_handback.sh:939/:941-959 · e2e.sh:107-130 | map purity cells / byte-identity 19 cells / refusal-token pin + C10b / non-systemd fence |

## 20. Implementation-time notes (owned by the implementer — NOT adjudicated here)

Demoted from earlier cycles; carried as implementer-owned notes, not body claims: (1) e2e-suite plumbing details (label naming, cleanup hooks, opt-in wiring) beyond the merge-gate requirement; (2) the precise stub-call-log format for the ordering/byte-identity asserts; (3) inventory/ordering niceties inside §16's standing items; (4) the `ENABLE_LAUNCHD_DISABLE_WINDOW` opt-in shape if ever revisited; (5) naming of the `unsupervised` declaration value's spelling in docs (the semantics are fixed in §6); (6) the exact wording of the loud operator messages (content fixed by §5.4/§15); (7) the fixture-file location/format for the captured redacted `launchctl print` sample; (8) any residual wording polish in the runbook. **None of these are claimed closed in §18.**

## 21. Leader scope decision + D2a/D2b split

**Decision (final — no fifth design round): D2 is scoped down.** This section is additive; §1–§20 above are unchanged and serve as the D2b basis.

### 21.1 Scope split

- **D2a — implemented now:**
  1. **Deterministic supervised FLIP** (M1 bootout barrier + M2 supervisor-owned bootstrap, §3) — no promote-started competing launcher on the supervised path.
  2. **launchd-aware 8b rollback using `$OLD_CUR`** (the promote.sh path, §5.4 mechanics as they apply to D2a's scope).
  3. **Fail-safe aborts:** named end state + halt-for-human + loud operator message — never a silent degrade.
  4. **Darwin fail-closed classifier** (auto-probe launchd first; declared `launchd`; garbage/`script` refusal) — implemented in **BOTH twins**.
  5. **SIGKILL mitigation** — owner-dead reclaim + watchdog alert (§14 note).
  6. **W1 / W4 / pass-A follow-ups** — carried by reference to the leader's pass-B dispatch.
- **D2b — deferred; §1–§20 of this doc are its basis:**
  - the **auto-rollback abort vehicle** (terminal-close ordering, lockless-vehicle machinery — the `_abort_restore_supervisor` contract);
  - the **`unsupervised` declaration value**;
  - the **full declared×verified outcome-map cells** for launchd;
  - the **8b/window auto-rollback**.

### 21.2 D2b backlog

**A. Approver iteration-004 findings — carried verbatim-by-reference.** The authoritative text (anchors + required changes) is the approver's iteration-004 report; this is the index only:

| # | Finding |
|---|---|
| F1 | verbatim by reference — approver iteration-004 report, finding #1 |
| F2 | verbatim by reference — approver iteration-004 report, finding #2 |
| F3 | verbatim by reference — approver iteration-004 report, finding #3 |
| F4 | verbatim by reference — approver iteration-004 report, finding #4 |
| F5 | verbatim by reference — approver iteration-004 report, finding #5 |
| F6 | verbatim by reference — approver iteration-004 report, finding #6 |
| F7 | verbatim by reference — approver iteration-004 report, finding #7 |
| F8 | verbatim by reference — approver iteration-004 report, finding #8 |
| F9 | verbatim by reference — approver iteration-004 report, finding #9 |
| F10 | verbatim by reference — approver iteration-004 report, finding #10 |

**B. Selected implementation notes (carried as-is):**

1. `ExitTimeOut` N ≥ **90** (not 70) when the operator finally raises it.
2. `make plist-install` has **no `.bak`** — snapshot the live plist before re-running it.
3. **Rung D token missing** — the garbage-value refusal needs its own named token (not just `supervision-not-verified`).
4. **Classifier cells 5/6d amendments** — the existing expectations need explicit same-PR edits (cell numbering per the twins suite).
5. **`EXECUTOR_ENV_ALLOWLIST` transport gap** — the supervision declaration must survive the executor env allowlist; otherwise the declared mode never reaches the classifier.
6. **"19 cells" stale** — the literal count is stale; the pin is count == list length (both twins).
7. **`:6022 → :6021`** — anchor drift in the global-token citation.
8. **§7 bootstrap-fail over-claim** — the bootstrap-failure row's end state needs the same three-way precision as the vehicle branches.
9. **Stop-failure DARK+DISARMED presumes bootout succeeded** — the row must state the case where bootout itself failed (supervisor still armed).
10. **Launcher-sweep cell vacuity** — the sweep-interaction cell must assert a real disposition change, not a tautology.
11. **Signal-entry disposition rule** — a signal-triggered abort must still record a disposition (the event carries it; the exit code carries attribution).
12. **B1 bound "<300 s at kill"** — state the heartbeat-age bound exactly as `< 300 s` at SIGKILL, not "≤30 s old" as a guarantee.

### 21.3 Explicit statement

**D2a does NOT rely on any of the rejected vehicle machinery** (§5.3's `_abort_restore_supervisor` contract, the terminal-close ordering, the three-disposition model, the lockless-vehicle discussion). D2a's aborts are **fail-safe**: they **refuse or halt** with a named end state and a loud operator message — **never degrade**. The **D2a implementation brief is the leader's pass-B dispatch**, not this document; §1–§20 remain the D2b basis and are not re-litigated here.
