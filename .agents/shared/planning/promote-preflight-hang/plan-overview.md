# promote-preflight-hang — Plan Overview

**Lane:** `fix/promote-preflight-hang` (worktree `/home/nea/ensemble-src-wt-preflight-hang`)
**Board item:** `wi-fc1786a2` · **Commission:** job `29ea7080-9bc1-47d7-9429-d0940d2b5488`
**Plan author:** plan author only — no implementation, no commits, no daemon boots, no test runs.
**Base rev (all line anchors below are verified against this rev):** `b1183a637649f94c23b94dfc745df44381dc54ab` (= the v0.27.13 bump commit = `origin/latest` tip at lane open; worktree clean).

> **ANCHOR CONVENTION.** Every `file:line` in this plan set is verified at **TIP `b1183a637`**; the HUNG-rev counterpart is given **only where the two differ materially** (the `lib.sh` line numbers shift by ~150 between the revs — the HUNG numbers appear in the architect's evidence, not in this plan set's anchors, except in `phase0-plan.md` §3.1's explicit HUNG column).
> The evidence docs cite **HUNG**-rev line numbers (blob `f81bcf6ba`, the live dev checkout at `013895287`).
> Where the two differ, the plan cites TIP. A HUNG counterpart appears in parentheses **only where the set
> actually carries one** (the `lib.sh` rows in §2 `phase2` §3.3a and `phase0` §3.1's explicit HUNG column — five
> anchors in total); the remaining divergent anchors carry **no** HUNG counterpart, because `phase0-plan.md` P0-D4
> mandates a **script-based** re-verification at the implementation rev rather than a hand-maintained dual column
> (approver-002 **N-1**, downgraded to a note for exactly this reason).
> The HUNG rev's code is recoverable read-only via `git show 013895287:scripts/upgrade/lib.sh` (commit) — or straight from the blob: `git cat-file -p f81bcf6ba`. **`f81bcf6ba` is a blob, NOT a commit** ⇒ `git show f81bcf6ba:scripts/upgrade/lib.sh` fails; see `evidence/CODE-PATHS.md` §1.

---

## 1. Objective

Deliver the fix lane for the 2026-10-28 promote-executor preflight hang (`run_id=r-20261028-170150-2d54`):
the promote preflight called a legacy **O(N²) per-character bash JSON balance-scan** (`journal_read`) on a
455,783-byte / 2,949-entry journal and burned 37 min 43 s of 100 % CPU without reaching the integrity phase,
without a journal row, without a heartbeat progression, and without any deadline able to fire.

Four deliverables:

| # | Deliverable | One-line statement |
|---|-------------|--------------------|
| **D1** | Root-cause fix | Make the journal **read** and **read-modify-write** paths O(N) with **exact** accept/reject semantics preserved, and census/bound every remaining unbounded-walk site in `scripts/upgrade/*` (incl. the stage-path manifest-checksum burn). |
| **D2** | Preflight watchdog | A **layered** mechanism that makes a stuck preflight **fail loudly** (journal row + non-zero exit + lock release) instead of hanging silently, and that **never** fires on a healthy slow phase. |
| **D3** | Durable logging | The executor's own narrative **survives parent death** (per-run log file as PRIMARY sink) and phase transitions are visible **mid-phase**. |
| **D4** | Secondary defects | (a) the **`stage.sh` lock-release protocol**: the release is **trap-only** ⇒ a **SIGKILL-class death orphans the lock dir**, and the **reclaim is not journaled** ⇒ the orphan confused the next operator (a normal, successful stage DOES release the lock — the corrected framing is in `phase4-plan.md` §2.1); (b) the daemon's `executor_still_running` "benign-detach (no kill)" disposition (routed). |

**Root cause is established FACT** (two independent evidence lanes: `evidence/FORENSICS.md`,
`evidence/CODE-PATHS.md`). This plan **cites** it; it does **not** re-derive or re-litigate it.
Refuted and therefore **out of scope**: broken-pipe stdout (payload stdio is systemd-owned
`StandardOutput=append:`, no daemon-side reader exists), `pg_dump` (zero occurrences), orphaned-lock causality
(lock acquired cleanly; the orphan was stage-side).

---

## 2. Scope

### In scope

* `scripts/upgrade/lib.sh` — `journal_read`, `journal_update`, `_json_sub`, and the journal-string extractors.
* `scripts/upgrade/journal_splice.py` — a read-only journal scanner op (or a sibling helper — see `decisions.md` D1).
* `scripts/upgrade/promote.sh` — the preflight phase: step budgets, in-phase watchdog, deadline checkpoints.
* `scripts/upgrade/stage.sh` — the manifest-checksum / tree-walk budget, the lock-leak defect.
* `scripts/upgrade/rollback.sh`, `restart.sh`, `status.sh` — read-path callers affected by D1.
* `daemon/tools/upgrade_journal.py` — per-run executor log path (D3), transient-unit property (D3/L3).
* `daemon/services/upgrade_journal_sweep.py` — **observation only** (D4(b) is routed, not changed).
* `daemon/tools/upgrade_tools.py` — the **arm path** (scripts provenance + the stamp/floor refusal) — **Phase 6** [fold: A28, A29].
* New test packs under `tests/packs/` + `tests/helpers/` (house shell-pack style).

### Out of scope (explicit)

* **No live promote, ever.** No `current` symlink flip, no live `state.json` promote-field write, no live daemon restart.
  All reproduction is sandboxed (§7 Test strategy).
* **No schema / migration change.** This lane ships shell + python pipeline changes only (see §8 Rollback posture).
* **No change to the journal FORMAT.** The read path is read-only; the write path is untouched by D1.
* **No change to the `benign-detach` kill contract** (D4(b)) — routed to its own commission (see `decisions.md` D4b).
* **No change to the MCP/init-guard journal writer's volume policy** (the 89 % `mcp_server_state` bloat driver,
  `daemon/mcp/init_guard.py:150`/`:561`) — routed to its owner; D1 adds a shell-side **size guard** only.
* **No re-litigation** of the refuted candidates.

---

## 3. Phases

| # | Name | File | Objective | Depends on |
|---|------|------|-----------|------------|
| 0 | Baseline reproduction & census lock | `phase0-plan.md` | Reproduce the original symptom in a sandbox at BASE; lock the full O(N²) site census and the verified anchor index; produce the fixture corpus. | — |
| 1 | **D1** — O(N) journal read / RMW | `phase1-plan.md` | Replace the per-character scans on the journal string with an O(N) implementation preserving exact semantics; bound the stage-path checksum burn; add the journal size guard. | 0 |
| 2 | **D2** — Layered preflight watchdog | `phase2-plan.md` | In-phase watchdog + deadline checkpoints + outer systemd bound + per-phase budgets sized from evidence. | 0, 1 |
| 3 | **D3** — Logging that survives anything | `phase3-plan.md` | Per-run log file as PRIMARY sink; stdout best-effort/non-blocking; mid-phase phase markers; EPIPE + client-pipe hygiene. | 0, 2 |
| 4 | **D4** — Secondary defects | `phase4-plan.md` | `stage.sh` lock-leak root-cause + fix; D4(b) disposition (routed). | 0 |
| 5 | Test & evidence | `phase5-plan.md` | Pin packs, mutation verification, sandbox repro BASE→HEAD, gate-chain runbook, stage adjudication inputs. | 1–4 |
| 6 | **Ceremony readiness** | `phase6-plan.md` | Make the fix actually reach the ceremony: deployment-truth chain, the user-owned dev-checkout sync precondition, daemon-seat provenance + stamp/floor refusal, watchdog token, runbook checklist. | 1–5 |

**Gate chain this plan feeds (house order):**
`architect enrichment → reviewer council → **count-sweep gate (W4)** → fresh approver → implement → code council → tidier → tester functional gate → giter stage → staged verification`.
The **count-sweep gate** is a new, **mechanical** pre-approver step: it asserts the plan set's counts and stale
literals (corpus **22 + G1–G5 = 27**, mutation matrix **M1–M32**, pack set **A–J**, **no** `RuntimeMaxSec=1200` pin
literal, no single-file-era wording) by an executable check, not by eye (`phase5-plan.md` T5.17 / AC17).
Phase 5 carries the exact command surfaces for the tester functional gate and the giter stage.
**Phase 6 is NEW** — created by the architect fold (amendments 26–31, RULING 9): the fix does not reach the ceremony unless the *pinned scripts directory* is handled, and the guard's seat is the DAEMON (the only component whose version the promote itself guarantees).

---

## 4. Coupling Map

Shared files/contracts between phases. Strength: **H** = hard (same file, ordering matters) · **M** = medium (shared contract) · **L** = loose.

| | P0 | P1 | P2 | P3 | P4 | P5 | **P6** |
|---|---|---|---|---|---|---|---|
| **P0** | — | H | H | H | H | H | M (the fence contract + the measured baselines feed P6) |
| **P1** | H | — | H (`journal_read` output is the watchdog's input) | M (log writer shares the file-append helper) | M | H | H (`lib.sh` carries the stamp P6 reads at arm time) |
| **P2** | H | H | — | M (watchdog row + phase markers both land in the journal/log) | L | H | M (the watchdog token + the floor both gate the executor) |
| **P3** | H | M | M | — | L | H | M (the per-run log path + the stamp's first line) |
| **P4** | H | M | L | L | — | H | L |
| **P5** | H | H | H | H | H | — | H (Pack I is the ceremony-readiness pack) |

**Contract seams (the four places where a phase can break another):**

1. **S1 — `journal_read` output contract.** `journal_read` returns the journal JSON on stdout and `1` on
   absent/empty/torn. **17 lib.sh call sites + 8 script call sites = 25 distinct call sites** depend on this (census: `phase0-plan.md` §4).
   Any D1 change MUST keep the stdout bytes and the verdict set identical.
2. **S2 — the journal write path.** `journal_history_append` → `_splice_journal` → `journal_splice.py`
   (`lib.sh:1106`/`:1161`). D1 must NOT touch the write path (it is already O(N)); D2's watchdog rows and
   D3's phase markers both WRITE through it ⇒ the watchdog row write must be lock-safe and must never
   re-enter a slow path (the row itself is the fast path).
3. **S3 — the transient systemd unit contract.** `build_service_argv` (`daemon/tools/upgrade_journal.py:2462`)
   + `unit_properties` (`:2468`, loop `:2502`) + the `Standard*Output=append:` pair (`:2536`-`:2537`).
   D3 changes the log path; D2/L3 adds a unit property. Both are pinned by python tests.
4. **S4 — the lock protocol.** `lock_acquire` (`lib.sh:1629`) / `lock_heartbeat` (`:1729`) / `lock_release` (`:1745`)
   + the stale-break rules (`:1670`-`:1694`). D2's watchdog MUST release the lock on breach;
   D4(a) touches the same protocol. The `journal_splice.py` live-install waiver ALSO keys off this lock
   (`journal_splice.py:158`/`:553`) ⇒ a D1 read helper that reuses `journal_splice.py` inherits this coupling.

---

## 5. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| **R1** | The watchdog **false-fires** on a healthy slow phase (the 300 s soak, `SOAK_S_DEFAULT=300` at `lib.sh:131`) and fails a legitimate ceremony. | **high** | Budgets are **phase-scoped** and the soak phase is excluded by construction; every budget is sized from a measured healthy baseline (`phase2-plan.md` **D2-11 / T2.19** + §3.6); a **negative-control pin** asserts 0 breaches across N healthy sandbox runs; budgets are env-overridable and journalled. |
| **R2** | The D1 read path changes the **torn-journal verdict set** (the balance scan is a *structural* check, not a JSON parse) and a refusal that used to fire now doesn't (or vice versa) ⇒ a torn journal is trusted. | **high** | Recommended implementation is a **byte-level port of the same scanner** (identical algorithm, identical accept/reject set), NOT `json.loads`; a **parity corpus** (**22 corpus cases + G1–G5 = 27 fixtures**: valid / empty / truncated / unbalanced `{` / unbalanced `[` / unclosed string / escaped quote / brace-in-string / multibyte / invalid UTF-8 / whitespace-only / newline-only / brace-free trailing garbage / brace garbage / NUL-in-string / duplicate-key / escape-then-multibyte ×2 locales / …) asserts **identical verdict AND identical stdout bytes** vs the BASE bash scanner; the change is **fail-closed** in direction (never newly ACCEPT a file the old scanner rejected). |
| **R3** | `python3` is not available on a path that today needs no interpreter (`status.sh:52` has **no** floor probe; `rollback.sh:90` and `restart.sh:133` read the journal **before** the lock is acquired). | medium | `promote.sh:209`, `rollback.sh:73`, `restart.sh:120` all floor-probe python3 ≥ 3.7 **before** their first `journal_read` ⇒ no new dependency on those paths. `status.sh` gets a floor probe or a bounded bash fallback (decision in `decisions.md` D1). `status.sh` is a **read-only status** tool ⇒ a missing interpreter must degrade to "journal unreadable", never to a crash. |
| **R4** | The read path inherits `journal_splice.py`'s **live-install refusal** (`:549`-`:560`: refuses a path under the live install unless the pipeline lock is held) and breaks `status.sh` (no lock), `rollback.sh:90` and `restart.sh:133` (both pre-lock). | **high** | The read path gets its **own policy**, not the write policy (decisions.md D1). Either a dedicated read-only helper, or an explicitly read-exempt op with a house ruling. The plan does **not** silently waive the live-install fence. |
| **R5** | The journal keeps growing (2,949 entries / 455 KB, 89 % `mcp_server_state` from `daemon/mcp/init_guard.py:150`) ⇒ D1 only **moves the cliff**. | medium | D1 adds a **journal size guard** (refuse + journal-warn above a byte threshold ⇒ loud, not a 61-minute stall) and the plan **routes** the writer-side coalescing/rate-limit to the MCP/init-guard owner (recorded as a routed item, not silently dropped). |
| **R6** | The sandbox repro resolves a live-shaped path and mutates the live install (the 2026-10-12 incident deleted two live installs). | **high** | `mktemp` FAKE_HOME + explicit escape guard (`case "$INSTALL_DIR" in /home/nea/agents-ensemble*) refuse``), per-variable `POSTGRES_*` scrub (**never** `env -u POSTGRES_*` — silent NO-OP), explicit PORT, scratch DB over TCP, never `rm -rf` on a resolved path without the guard. Precedent: `tests/helpers/soak_gate_pack_common.sh:1`-`:38`. |
| **R7** | The per-run log (D3) breaks the daemon's log-tail reader (`upgrade_tools.py:941`-`:966`, bounded 256 KB tail, constant at `:209`). | medium | Keep `data/upgrade.log` as the aggregate sink **and** add the per-run file; the reader gains the per-run path as an additional source; the existing reader keeps working unchanged (additive). |
| **R8** | A systemd `RuntimeMaxSec` (D2/L3) fires during a healthy long promote. | high | L3 is a **backstop only**, budgeted **above** the healthy total (`PROMOTE_TOTAL_DEADLINE_S=900` at `lib.sh:109`, healthy total ≈ 490 s per `daemon/services/upgrade_journal_sweep.py:98`-`:102`); the primary mechanism is the in-shell per-phase watchdog (L2). If the architect prefers L3-primary, the budget rule in `phase2-plan.md` **D2-9 / T2.17** (§3.2) applies unchanged. |
| **R9** | The sibling event-loop sync-DB lane (`wi-3f5bea95`) stages first and takes the version slot. | low | Version slot is derived by **live census at stage time** (no reservation) — the lane owner re-derives. This lane ships **no** version claim in the plan. |
| **R10** | The 37-minute blind window persists because the daemon's benign-detach still does nothing (D4(b) routed). | medium | D3 makes the stall **correlatable** (per-run log path + phase markers + the last phase in the journal row) so the operator can see *where* it stalled even before L4 lands; the D4(b) routing is recorded in the project history by the lane owner, not silently dropped. |
| **R11** | The D1 helper's runtime cost regresses the **`status.sh`** UX (a status call that used to take 11 s at 25 KB now takes 0.02 s — fine) but a **cold** `python3` start adds ~30 ms per call × 17 lib.sh sites. | low | The helper is called at most a handful of times per pipeline run; the O(N) win dominates by 4–5 orders of magnitude at realistic journal sizes (455 KB: 0.024 s python vs ≈ 3,670 s bash, per `evidence/CODE-PATHS.md` §12). |

### 5.1 Risk register — architect additions [fold: A33]

Appended verbatim from `architecture-recommendation.md` §2 (the architect's own IDs; they do not collide with R1–R11
above). **Disposition = the architect's ruling**, encoded in the phase files named in the last column.

| ID | Risk | Sev | Disposition | Encoded in |
|----|------|-----|-------------|------------|
| **R-A** | The fix does not reach the ceremony: pinned scripts checkout is 277 commits behind and still carries the O(N²) scan (MEASURED); shell-side watchdog absent, L3-only present — primary/backstop inverted | 🔴 | RULING 9 (precondition + daemon-seat provenance + floor refusal + watchdog token) | `phase6-plan.md` **D6-1 / D6-2 / D6-4 / D6-10 / D6-11** · **T6.2 / T6.4 / T6.5 / T6.9 / T6.11** · `plan-overview.md` §9 A6 + §11 + SC12 |
| **R12** | `STAGE_TREE_BUDGET_S=300s` false-fires on a healthy stage (plugins walk measured 329.6 s/pass, walked twice ⇒ ≈660 s) — aborts a good stage | 🔴 | RULING 6b: batch the hashing + derive the budget from the re-measured walk | `phase2-plan.md` **D2-10 / T2.18** (A7) |
| **R13** | Any PID-targeted kill is trap-bypassing (MEASURED): no halt row, no lock release — a future daemon reaper or operator drill can silently reproduce the incident | 🔴 | RULING 5 invariant: cgroup/process-group-wide signals ONLY, pinned in Phase 0 | `phase2-plan.md` D2-13 / T2.22 (A10), `phase0-plan.md` T0.11 (A10) |
| **R14** | TERM-trap re-entrancy ⇒ duplicate `halt` rows (MEASURED); the journal is not idempotent | 🔴 | RULING 5: `_HALT_JOURNALED` guard + double-TERM pin | `phase2-plan.md` D2-4 / T2.12 (A1) |
| **R15** | Breach journal rows are unachievable on unlocked paths (the live-install splice fence refuses them); `status.sh` must never mutate the journal | 🔴 | RULING 5: unlocked-path split; AC2 amended; `status.sh` exits 1 | `phase2-plan.md` **D2-5 / T2.13** (A2) + **D2-6 / T2.14** (A3), SC4b |
| **R-N1** | Parity is defined against `$(cat)`-processed bytes (trailing `\n` stripped, NULs dropped — MEASURED); a raw-dump helper fails stdout parity on every real journal | 🔴 | RULING 8: command-substitution emulation contract + corpus cases | `phase1-plan.md` D1-10 / T1.11 (A14) |
| **R-N2** | Duplicate-key `journal_update` corruption is BASE behavior (MEASURED); an un-pinned parity test flags the faithful port as defective or invites a silent behaviour change | 🔴 | RULING 8: pin the exact corrupt bytes as BASE-bug-preserving | `phase1-plan.md` D1-11 / T1.12 (A15), corpus case 21 |
| **R16** | `RuntimeMaxSec` set at the shared `spawn_executor` also bounds the **restart** unit — a different ceremony with different budgets | 🟡 | RULING 6c: kind-scoped to `kind=="promote"` | `phase2-plan.md` **D2-8 / T2.16** (A5) |
| **R17** | `ENSEMBLE_PROMOTE_SOAK_S` accepted up to 900 ⇒ constant L3=1200 fires mid-soak **after the symlink flip** | 🟡 | RULING 6c: derived RuntimeMaxSec + refuse SOAK_S>300 in live mode | `phase2-plan.md` **D2-9 / T2.17** (A6) |
| **R-B** | Log lines ≥8192 B corrupt under the concurrent writer (MEASURED; livez/readyz JSON logs are unbounded) | 🟡 | RULING 7b: 4 KiB cap + elision + newline sanitization | `phase3-plan.md` D3-11 / T3.12 (A21) |
| **R-C** | Payload stdout is block-buffered ⇒ not real-time in the `append:` capture — ordering illusion in a single file | 🟡 | RULING 7c: two-file sink; markers are direct writes | `phase3-plan.md` D3-12 / T3.13 (A22) |
| **R-D** | Retention can delete a stalled live run's file (stale mtime; systemd holds it open ⇒ narrative vanishes into an unlinked inode) | 🟡 | RULING 7a fences (<6 h mtime, in_flight, pointer, lock-held) | `phase3-plan.md` D3-10 / T3.11 (A20) |
| **R-E** | The sweep's `_log_tail` reads the aggregate D3-1 abandons ⇒ `executor_exit` loses the run's tail | 🟡 | RULING 7e: sweep reads per-run path via run_id | `phase3-plan.md` **D3-14 / T3.15** (A24) |
| **R-F** | A sandbox ceremony resolves the LIVE install root (frozen-binary path derivation) ⇒ per-run logs pollute the live audit/retention domain | 🟡 | RULING 7f: env-scoped `data/upgrade/<env>/` | `phase3-plan.md` D3-15 / T3.15 (A22, A24) |
| **R-G** | A scripts-dir guard firing after the nonce burn consumes the ceremony it protects | 🟡 | RULING 9 sequencing: refusal before `consume_pending_action` | `phase6-plan.md` **D6-4 / T6.5** (A29 — the nonce-burn sequencing) + **T6.6** (A30 — the watchdog token) |
| **R18** | Shell-side watchdog lives in the unpinned tree; stale checkout promotes with no primary watchdog | 🟡 | RULING 9(iii)/(iv) — merged into the R-A disposition | `phase6-plan.md` (A29, A30) |
| **R19** | `_run_bounded` TERMs the direct child only — a grandchild survives; harmless for read helpers, fatal if the wrapper is reused on a write path | 🟡 | RULING 5/A1: wrapper contract note; process-group kill for side-effecting steps; never reuse on write paths | `phase2-plan.md` **D2-14 / T2.21** (A11) |
| **R20** | Trap-install window (lock held, no handler) ⇒ TERM there orphans the lock with no row (the D4a shape) | 🟡 | RULING 5: install traps before `lock_acquire` | `phase2-plan.md` **D2-7 / T2.15** (A4) |
| **R-N3** | "Same state machine" phrasing invites sharing the `in_str` scanner across all three verbs — changes the extraction set | 🟡 | RULING 8: three-algorithms rule, per-verb pins | `phase1-plan.md` D1-12 / T1.13 (A16) |
| **R21** | `_run_bounded` collapses a child's legitimate 143/137 to 124 | 🟢 | Document in the wrapper contract | `phase2-plan.md` **D2-14** (A11) |
| **R22** | Operator docs that read exit-78 alone misfile a watchdog breach as a stage-guard refusal | 🟢 | RULING 4 runbook amendment: read kind + detail | `phase2-plan.md` **D2-12 / T2.20** (A9) |
| **R-H** | `_log_tsl` already exists (portable, failure-signalling, unwired); `lib.sh:358-360` inverts the stream description | 🟢 | RULING 7e: reuse; fix doc in the same hunk | `phase3-plan.md` **D3-14 / T3.15** (A24) |
| **R-I** | `_json_sub` is quadratic twice (`out="$out$char"` AND `rest="${rest:1}"`) — the census must not over-claim SC3 coverage | 🟢 | RULING 8: both mechanisms in the port scope | `phase1-plan.md` **D1-12 / T1.13** (the three-algorithms rule: both `_json_sub` quadratic mechanisms are in the port scope) |
| **R23** | The journal **size guard** could block the **emergency rollback path** — a guard added for promote hygiene would turn degradation into **no-recovery** | 🔴 | **LEADER RULING Q3 (W2, DISCHARGED)**: refuse on the **promote path only**; `rollback.sh` / `restart.sh` / `status.sh` get **WARN + CONTINUE**; the threshold + env override are stated and derived; the runbook carries the trim/recovery step — **phase1 D1-14 / D1-17**, T1.15, AC16 | `phase1-plan.md` **D1-14** (the Q3 path scope) + **D1-17** (the re-scoped semantics) · `phase5-plan.md` Pack J |
| **R26** | A **bare size threshold** on the guard would **false-refuse the next ceremony**: the live journal is **already 455 KB+** and legitimately growing, and the post-fix read path is **O(N)** python ⇒ a size police on healthy reads would block the very ceremony this lane protects (while an implied 600 KB default would **not** have fired on the 455,783 B incident configuration at all) | 🔴 | **ROUND-4 DIRECTIVE (W1): the guard is RE-SCOPED to a LEGACY-CLASS REGRESSION BACKSTOP** — it trips only when the code is about to fall back to the unbounded bash walk **AND** the journal ≥ `JOURNAL_SIZE_GUARD_BYTES` (98304); the **O(N)** path never consults it ⇒ the incident configuration trips, the next ceremony does not — `phase1 D1-17`, T1.18, AC19, **phase5 Pack J** | `phase1-plan.md` **D1-17** (the re-scoped legacy-class backstop) · `phase5-plan.md` Pack J legs J5/J6 |
| **R24** | The **git-ancestry floor** can produce a rare **false refusal**: a **cherry-picked** fix (content present, fix commit absent) or a **shallow clone** lacking the floor object ⇒ refuse, though the tree may carry the fix | 🟡 | **LEADER RULING Q1**: explicitly accepted — "a tree that does not contain the fix commit object cannot contain the fix; the remediation is a proper fetch/merge". The floor-object-missing case is **REFUSE by ruling**. Risk is recorded in the runbook (a refusal names the sync command) — `phase6-plan.md` §4.2, D6-10 | `phase6-plan.md` **D6-10** (the Q1 hybrid floor) + **T6.9** + `decisions.md` D6-10 |
| **R25** | An **out-of-window write path** gets wrapped by the watchdog (a kill mid-write leaves half-done state) — e.g. `retention_evict` (defined at `lib.sh:5526`; its read at `:5528`; called at `promote.sh:571`), which is **mutating** (`rm -rf` of release dirs) | 🟡 | **W1**: §3.3 is split into **in-window** / **out-of-window** tables with a *side-effecting?* column; `retention_evict` is marked **MUST-NOT-WRAP**; the commit-phase sites are named as **deliberately uncovered**; D2-14(ii) forbids wrapping write paths; the process-group kill is a **NEW variant** (the shared primitive stays byte-unchanged) — `phase2-plan.md` §3.3a/§3.3b, T2.24, AC24 | `phase2-plan.md` **§3.3a / §3.3b** (the in-window / out-of-window split + the side-effecting column) + **D2-14 / T2.24** (the MUST-NOT-WRAP + the new-variant decision) |

---

## 6. Success Criteria

Measurable and verifiable. Every SC is asserted by a named pin in `phase5-plan.md`.

| ID | Criterion | Measurement |
|----|-----------|-------------|
| **SC1** | `journal_read` on the **455,783-byte incident fixture** completes in **< 5 s** at HEAD (BASE: **≥ 45 min**, measured 61 min projected / 2,263 s observed at 62 %). | `phase5-plan.md` Pack B timing leg |
| **SC2** | Read-path **semantics parity**: over the **22 corpus cases + G1–G5 = 27 fixtures** **[fold: A15, W4]**, the new path returns the **identical verdict** and **identical stdout bytes** as the BASE bash scanner in **100 %** of cases; the stdout contract is **command-substitution emulation** (trailing `\n` stripped, NULs dropped) **[fold: A14]**. | Pack A |
| **SC3** | **Zero** remaining per-character scans over the full journal string on the promote path; every `journal_read`/`journal_update`/`_json_sub` call site is enumerated and either migrated or explicitly exempted with a rationale. | Pack A census leg + code review |
| **SC4** | A preflight step that exceeds its budget terminates within **budget + 10 s**, with: a `deadline-breach-*` journal row, a `_warn` line on stderr, the lock dir **released**, and a **non-zero** exit. | Pack D (planted slow step) |
| **SC4b** | **Unlocked-path breach**: on a path that holds no pipeline lock (`rollback.sh:90` / `restart.sh:133` / `status.sh:52`) the breach produces **warn + per-run log only, NO journal row** (the live-install splice fence refuses it), and `status.sh` exits **1** — never 78, never a journal mutation. **[fold: A3]** | Pack D (unlocked leg) |
| **SC5** | The watchdog **never** fires on a healthy path: **0** breaches and **0** kills across **N ≥ 3** healthy sandbox runs including a **REAL 300 s conservative-mode soak** (not a simulated phase inside the preflight window) **[fold: A8]**. | Pack D negative control |
| **SC6** | The executor narrative **survives parent death**: after SIGKILLing the parent client, the per-run log still contains the last phase marker and the terminal row. | Pack E |
| **SC7** | Phase transitions are visible **mid-phase**: the per-run log contains **≥ 1 marker per preflight step** (≥ 6 markers on the promote preflight). | Pack E |
| **SC8** | `stage.sh` leaves **no** lock dir after a successful run; a stale/dead-owner orphan is reclaimed **loudly** (a journal row naming the reclaim + the dead owner pid). | Pack F |
| **SC9** | **No schema change**: the migration-file set is **EMPTY** vs the live release; the journal on-disk format is byte-compatible. | giter stage adjudication + Pack A |
| **SC10** | Every pin is **mutation-verified**: plant the defect ⇒ the pinned assertion turns **RED**; restore ⇒ **GREEN**; the planted digests are recorded in the tester RESULTS. | Pack G (mutation matrix) |
| **SC11** | The sandbox repro is **closed**: the original symptom (BASE: preflight spins on a large journal) is **reproduced at BASE** and **absent at HEAD** on the same fixture, same command, same sandbox. | Pack C |
| **SC12** | **Ceremony readiness**: a staged sandbox arm against a **provably-stale pinned scripts dir** **REFUSES BEFORE the nonce is consumed** (no nonce burn, a refusal row naming the sync command); with a synced dir the arm proceeds cleanly. **[fold: A29, A34; B1, B3]** | Pack I |
| **SC13** | **Size-guard scope + semantics (leader ruling Q3 + the round-4 directive)**: the guard trips **only** on the **legacy-class bash walk** (the python helper absent/unusable) **AND** a journal ≥ `JOURNAL_SIZE_GUARD_BYTES` (default **98304**); on the **O(N)** path it is **not consulted** ⇒ the next ceremony (a 455 KB+ live journal) is **never blocked**. When it does trip: **refuse the promote** (loud, size-named) and **warn + continue** on `rollback.sh` / `restart.sh` / `status.sh` — the emergency recovery path is never blocked by a promote-hygiene guard. **[fold: W2, Q3, W1]** | **phase1 T1.18 / AC19** (the re-scoped guard) + **phase1 T1.17 / AC18** (the matrix cell) + **phase5 Pack J** |
| **SC14** | **Staged-verification split (W5)**: the **daemon-side** surface is verified from the **staged artifact**; the **shell** surface is verified from the **pinned checkout at the synced rev** (payloads ship **no `scripts/`** since v0.27.4); the arm row **requires** both `git rev` and `lib.sh` sha256 as provenance fields. **[fold: W5]** | Pack I |

---

## 7. Test Strategy (summary — the mechanics live in `phase5-plan.md`)

* **Directional pin packs** in the house shell-pack style (`tests/packs/*.sh` + `tests/helpers/*.sh`),
  not ad-hoc pytest. Precedent: `tests/packs/promote_soak_never_green_test.sh`,
  `tests/helpers/soak_gate_pack_common.sh`, `tests/test_journal_splice.sh`.
* **Mutation-verified** (plant → RED → restore → GREEN), per house rule
  *"regression pins MUST be mutation-verified"*.
* **BASE→HEAD repro** of the original symptom in an isolated sandbox (the soak-gate lane's N4 precedent:
  scratch install root, per-variable env scrub, `mktemp` FAKE_HOME fences).
* **No BASE-scale timeouts in CI**: the BASE 455 KB scan is ~45–61 min ⇒ the pack asserts a **scaling ratio**
  (T(4N) < 6×T(N)) and a **HEAD absolute** (< 5 s at 455 KB), never a BASE 45-minute run.
* **Sandbox fences** (all mandatory, all asserted inside the pack):
  `mktemp` FAKE_HOME + escape guard · per-variable `POSTGRES_*` scrub (never `env -u POSTGRES_*`) ·
  explicit PORT · scratch DB over TCP · no live path resolution · no daemon boot · no live promote.
* **Net-new reds = 0** vs the TRUE pre-fix base (same commands both sides), per the house net-new-failure rule.

---

## 8. Rollback posture (for the stage adjudication)

**Position: `rollback_safe = true`, source = explicit (standing-rule semantic reasoning).**

1. **No schema mutation ships.** No `.sql` file is added or changed ⇒ the migration delta vs the live release
   is **EMPTY**; the old binary and the new binary read the same schema.
2. **No journal-format change.** D1 replaces *how* the journal is read, not *what* it contains. The journal
   written by the new code is byte-compatible with the old code's writer (the writer path is untouched).
3. **Shell + python pipeline changes only.** `scripts/upgrade/*.sh`, `scripts/upgrade/journal_splice.py`,
   `daemon/tools/upgrade_journal.py`. The daemon's runtime behaviour changes only in the executor-launch
   path (log path + unit property) — both additive.
4. **Rollback direction.** Rolling back to the previous release restores the **O(N²) read** ⇒ the pre-existing
   hang risk returns. This is a **capability regression on rollback, not an incompatibility**: the old code
   keeps working, it is just slow again. Disclosed explicitly in the stage adjudication, in the tag annotation,
   and in the promote-time runbook (the operator must not treat a rolled-back release as "fixed").
5. **Auto-rollback safety.** A failed promote gate may auto-rollback to the previous release; that path is
   unaffected by this lane (no schema, no journal shape, no release-dir layout change).
6. **🔴 Coupling disclosure — the size guard vs the journal-growth fix (Adv6, UPDATED for W1).** The **journal size
   guard** (**phase1 D1-17** — the re-scoped LEGACY-CLASS backstop) and the **writer-side journal-growth policy**
   (routed to the `mcp_server_state` owner, `daemon/mcp/init_guard.py:150` — 89 % of the 455 KB journal) are
   **coupled by design**. **After the round-4 re-scope the coupling is materially weaker than the round-3 text
   claimed:** the guard is consulted **only** when the read would fall back to the **legacy-class bash walk** — so
   on the **healthy O(N) python path** a growing 455 KB+ journal raises **no** guard action at all, and the
   **growth-policy work is NOT on the promote's critical path**. What remains coupled: if the **python helper is
   unavailable on a host** (a walk-class regression) *and* the journal is ≥ 98304 B, the guard refuses the promote
   (loud, size-named) and **warns + continues** on `rollback.sh` / `restart.sh` / `status.sh` ⇒ **recovery is never
   blocked** (leader ruling Q3). This coupling is disclosed at **stage adjudication** and in the **promote-time
   notes**: a promote refusal citing the size threshold means **"the code is about to walk the journal the slow way"**
   — a *walk-class / helper-availability* signal — **not** a journal-growth policy signal.

---

## 9. Assumptions & Open Questions

### Assumptions (explicit, flagged)

* **A1** — `b1183a637` remains the base for the implementation work; if `origin/latest` advances before
  implementation, the lane owner re-bases and re-verifies the anchor index (`phase0-plan.md` §2).
* **A2** — The `journal_read` **acceptance set is a contract**, not an implementation detail. If the architect
  rules that a strict JSON parse is acceptable instead of a byte-level scanner port, the R2 risk profile changes
  and the parity corpus must be re-specified (the plan's recommended option keeps the set identical).
* **A3** — `python3 ≥ 3.7` is available on every host that runs the upgrade pipeline (house assumption, already
  load-bearing: `promote.sh:209`, `rollback.sh:73`, `restart.sh:120` refuse without it).
* **A4** — The healthy promote wall (≈ 490 s minimum, `daemon/services/upgrade_journal_sweep.py:98`-`:102`)
  and the 300 s soak (`lib.sh:131`) are the sizing inputs for every watchdog budget. If the soak cap changes
  in the sibling lane, the budgets in `phase2-plan.md` must be re-derived.
* **A5** — The journal is expected to keep growing until the writer-side coalescing lands; D1's size guard is
  the interim loud-failure mechanism, not a capacity fix.

* **A4a** — *Extends A4* **[fold: A32]**: the L3 `RuntimeMaxSec` is **DERIVED, never a constant** —
  `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)` (= 1320 with the defaults) — computed at arm time. If the
  sibling lane changes soak semantics, the derivation (and the `SOAK_S > 300` live-mode refusal) must be
  re-derived, not re-tuned.
* **A6** — *NEW* **[fold: A32, A27]** — **the dev-checkout sync precondition is a CEREMONY BLOCKER owned OUTSIDE
  this lane.** The live `.env` pins `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`, and that
  checkout is **277 commits behind** `b1183a637` and **still contains the O(N²) scan** (MEASURED — architect
  RULING 9). Until the user (or the WIP owner of the shared checkout) syncs that directory so that its HEAD
**contains `PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ancestor** — i.e. `git -C <dir> merge-base --is-ancestor <floor> HEAD`
**passes** **[fold: W4 — the ordering predicate is ancestry, not `≥`]**
  **or** moves the pin, the next ceremony would run the OLD code. This lane is STAGE-ONLY and **cannot and will
  not** touch the shared checkout or the live `.env` pin. The in-lane interlock is the **daemon-side arm-time
  guard** (refuses a provably-stale pinned dir **before the nonce burns**) — so without the sync the next ceremony
  **REFUSES LOUDLY instead of hanging**: that is the correct failure mode. See `phase6-plan.md` and SC12.

### Open Questions (for architect / leader ruling)

> **OQ1 — D2 final shape.**  → **RESOLVED 2026-10-29 by RULING 1 (ACCEPTED-BY-LEADER)** — the recommended
> L2a + L1 + L3-backstop + L4-routed shape is ACCEPTED, with the mandatory amendments A1–A16 folded into
> `phase2-plan.md` (see the fold log, §12). No re-litigation.
>
> **OQ1 (original text, retained):** The commission explicitly reserves the final watchdog shape for the architect.
> The plan presents **four layers (L1–L4)** with trade-offs in `decisions.md` D2 and **recommends L2-primary
> + L1 + L3-as-backstop + L4-routed**. The architect must rule: (a) is the recommended shape accepted, or
> (b) is L3 (`RuntimeMaxSec`) to be primary, or (c) is L4 (daemon-side escalation of `executor_still_running`)
> to be pulled INTO this lane? If (c), the lane's blast radius grows from "shell + one daemon log-path change"
> to "daemon reaper contract change" and Phase 2's budget/negative-control design must be re-reviewed.

> **OQ2 — D1 read-path refusal policy.**  → **RESOLVED 2026-10-29 by RULING 2 (ACCEPTED-BY-LEADER)** — option
> **P1**: a dedicated read-only helper at the **new sibling file `scripts/upgrade/journal_read.py`**, with **no lock
> requirement and no install-fence**; the read path is **forbidden** to route through `_splice_journal`
> (amendment 19). Folded into `phase1-plan.md` **D1-13** (the placement + the forbidden-routing clause).
>
> **OQ2 (original text, retained):** `journal_splice.py` refuses to touch a live-install journal unless the
> pipeline lock is held (`journal_splice.py:553`), but `status.sh:52` reads it with **no** lock and
> `rollback.sh:90`/`restart.sh:133` read it **before** acquiring the lock. The plan recommends a **read-only
> helper with its own policy** (reads are not mutations; the live-install *write* fence stays intact). The
> architect must rule whether (a) a dedicated read-only helper is acceptable, or (b) the live-install fence must
> also gate reads (which would require moving the `rollback.sh`/`restart.sh` reads after lock acquisition and
> giving `status.sh` an explicit exemption).

> **OQ3 — D4(b) disposition.**  → **RESOLVED 2026-10-29 by RULING 3 (CONFIRMED-BY-LEADER)** — the
> benign-detach kill-contract change stays **ROUTED to its own commission**, with the three mandatory scope items
> (pre-txn stall signal · the "stalled" predicate · the writer-side journal-growth policy) written into the routed
> board item (amendment 35). Folded into **`decisions.md` D4b-4** (defined there; `phase4-plan.md` defines **no**
> decision rows — see §11.1 — and carries the implementing task T4.7 / AC7).
>
> **OQ3 (original text, retained):** The plan recommends **routing** the benign-detach kill-contract change to its
> own commission (D4(b) → separate board item), on the grounds that killing a detached executor is a policy
> decision with a real blast radius (it could kill a legitimate long promote whose daemon-side view is stale).
> The leader must rule: (a) confirm the routing, or (b) pull the escalation into this lane — in which case the
> Phase 4 scope grows and the `executor_still_running` predicate needs its own evidence lane (a "stalled" test,
> not a timer).

---

## 10. Evidence citations (read-only, never modified)

| Artifact | Use in this plan |
|----------|------------------|
| `evidence/FORENSICS.md` (266 L, sha256 `480b6250e18c…`) | Timeline, verbatim artifacts, measurements, the four refuted candidates, board-item transcription, premise corrections (§8). |
| `evidence/CODE-PATHS.md` (356 L) | Call graphs at both revs, lock/heartbeat mechanics, stdout wiring, unbounded-wait census (§6), the fix-surface map (§11). |

Both are cited as established fact. **No file under `evidence/` is modified by this lane.**

---

## 11. Ceremony readiness (Phase 6) — the fix only lands if the *scripts dir* is handled

**This section is NEW — created by the architect fold [fold: A26–A31, RULING 9].** The full plan is
`phase6-plan.md`. The short form:

1. **Deployment truth (MEASURED, architect RULING 9).** The scripts dir is resolved **once, at arm time, in the
   daemon** (`_resolve_scripts_dir`, `daemon/tools/upgrade_tools.py:1953-1966` → frozen into the arm marker at
   `:3714`); the path travels in **argv** (`daemon/manager.py:5494-5496`), not in the executor env (the env var is
   stripped by `EXECUTOR_ENV_ALLOWLIST`, `daemon/tools/upgrade_journal.py:2156-2162`); release payloads ship **no
   `scripts/`** since v0.27.4 (`stage.sh:392-425`) ⇒ **a checkout is the only scripts source on this host**; the
   live `.env:107` pins `/home/nea/ensemble-src/scripts/upgrade`, whose HEAD `013895287` is **277 commits behind**
   `b1183a637` and whose `lib.sh` **still contains the O(N²) scan**. ⇒ **the next ceremony runs the OLD code.**
2. **The hard precondition is USER/leader-owned** [fold: A27]: sync `/home/nea/ensemble-src/scripts/upgrade/` to
   its HEAD **contains `PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ancestor** — i.e.
   `git -C <dir> merge-base --is-ancestor <floor> HEAD` **passes** (scoped to `scripts/`; the checkout carried **15** unrelated dirty files at the architect's 2026-10-29
   measurement — **time-dependent**: a later count showed **14 modified + 49 untracked**, and the load-bearing
   predicate is only that **`scripts/` is clean**, which holds — that the **WIP owner**
   reconciles — the cross-lane WIP-owner precedent from the v0.27.1 lane) **or** move the pin. **This lane is
   STAGE-ONLY and must not touch the shared checkout or the live `.env`.**
3. **The in-lane interlock is the DAEMON-side arm-time guard** [fold: A29; **B1, B3 — council round 3**]: the
   daemon opens `<scripts_dir>/lib.sh` at **arm time** and parses `PIPELINE_SCRIPTS_REV=` from a **bounded
   first-N-KB read**, **before `consume_pending_action`** (the executor-start echo is demoted to a **corroborating**
   signal — it arrives after the nonce burn and cannot gate anything). The floor is the **Q1 HYBRID, primary =
   GIT ANCESTRY**: `PIPELINE_SCRIPTS_FLOOR_COMMIT` (owner = the release process; storage = daemon constants + the
   release manifest; cadence = the same as the `PIPELINE_SCRIPTS_REV` stamp — they travel together) and a
   read-only bounded `git -C <dir> merge-base --is-ancestor <floor> HEAD` ⇒ **pass iff ancestor**; a
   **missing/unknown floor object or command error ⇒ REFUSE**. A **non-git** pinned dir is **WARNING-ONLY**
   (Q2): WARN + a journal row carrying the pinned `lib.sh` sha256 as provenance — **never a refusal**. Without the
   sync the ceremony then **refuses loudly instead of hanging** — the correct failure mode.
4. **Defense in depth** [fold: A30]: the executor env carries `ENSEMBLE_PROMOTE_WATCHDOG=1`; `promote.sh` refuses
   loudly when the token is present but the watchdog is not armed (mixed-generation deployments).

**Rollout / efficacy.** The lane's efficacy is **conditional on the precondition**: with the sync in place the
ceremony runs the fixed scripts (D1 read is O(N), the watchdog is armed); without it the ceremony **refuses at arm
time** (SC12) and no ceremony is consumed. The staged-verification lane therefore asserts **both** — that the
guard exists **and** that it fires on a stale fixture — and the runbook gains a checklist item [fold: A31]:
*"ceremony scripts provenance verified — the arm row shows the expected stamp."*

---

## 11.1 Cross-file ID index (the W4 ID-style drift + the W5 `D1-*` collision — both closed)

Each file continues **its own** ID convention (house Mode-2 rule: never renumber across files); this index makes
every ID unambiguous across the set. The **topic** column is what resolves a cross-file id: two files may use the
same id for different topics, so **every cross-reference is file-qualified** (`phase1 D1-14`, `decisions.md D1-12`).

| File | Decision IDs | Topic of the decision series | Task IDs | Acceptance IDs | Other |
|------|---------------|-------------------------------|----------|----------------|-------|
| `plan-overview.md` | — (the register of record for SC/R) | — | — | — | SC1–SC14 (SC4b) · R1–R11 · R-A · R12–R26 · R-N1–R-N3 · R-B–R-I · A1–A6 |
| `phase0-plan.md` | P0-D1…P0-D9 | baseline reproduction + census lock | T0.1…T0.14 | AC1…AC14 | fixtures + the 26-row census (+ §4.2 companion) |
| `phase1-plan.md` | **D1-1…D1-17** | the journal **read/RMW** path | T1.1…T1.18 | AC1…AC19 | corpus cases 1–22 + G1–G5 = 27 |
| `phase2-plan.md` | **D2-4…D2-15** (D2-1…D2-3 exist ONLY in `decisions.md` — the ruling register) | the **watchdog** | T2.1…T2.24 | AC1…AC24 | budget tables §3.2 / §3.3a / §3.3b · §3.4b · §3.5 |
| `phase3-plan.md` | **D3-1…D3-16** (D3-1…D3-9 are the **superseded** single-file-era series; D3-10…D3-16 the live ones) | the **logging** | **T3.1…T3.16** (dotted form — the canonical task-id form, N2) | AC1…AC16 | two-file sink (D3-12) |
| `phase4-plan.md` | **none defined here** — the D4a-*/D4b-* decisions live in `decisions.md` (this file cites them) | the **secondary defects** | T4.1…T4.7 | AC1…AC7 | — |
| `phase5-plan.md` | — (the pack set) | — | T5.1…T5.17 | AC1…AC17 | **packs A–J** · **mutations M1–M32** |
| `phase6-plan.md` | **D6-1…D6-6, D6-10, D6-11** (D6-7…D6-9 live only in `decisions.md`) | **ceremony readiness** | T6.1…T6.15 | AC1…AC15 | — |
| `decisions.md` | D1…D6 (the ruling register) | one series per deliverable family | — | — | the **cross-file `D1-*` map** below |

### The `D1-*` cross-file map (W5 — the colliding pair is closed)

`phase1-plan.md` and `decisions.md` both carry a `D1-*` series; the two collided on the **emergency-recovery
exemption** and the **single-read guard**. **Round-4 normalisation (reviewer-directed):** the **Q3 exemption is
cited as `phase1-plan.md` D1-14**, the **single-read guard as `decisions.md` D1-12**. The two colliding `decisions.md`
entries were **swapped** (D1-12 ↔ D1-14) with a **mapping table** (in `decisions.md`) — no silent renumber.

| Topic | `phaseN-plan.md` id | `decisions.md` id | **Canonical** |
|-------|---------------------|--------------------|------------------------|
| the byte-level port — anchor: *"the verdict-input domain the $(cat)-processed bytes"* | D1-1 | D1-1 | phase1 D1-1 |
| the strict-JSON option (**declined**) — **no phase1 id** | — | D1-4 | decisions D1-4 |
| the stdout contract (`$(cat)` emulation) — anchor: *"the helper's stdout contract bash command-substitution"* | D1-10 | D1-9 | phase1 D1-10 |
| the corpus 15→22 + duplicate-key BASE-bug-preserving — anchor: *"the corpus grows and the duplicate-key"* | D1-11 | D1-10 | phase1 D1-11 |
| the three-algorithms rule — anchor: *"three distinct algorithms, not one "same"* | D1-12 | D1-11 | phase1 D1-12 |
| the placement = the new sibling file `journal_read.py` — anchor: *"placement final: dedicated read-only helper the"* | D1-13 | D1-8 | decisions D1-8 |
| the **Q3 path scope** (promote-path-only refusal) — anchor: *"the size guard refuses the promote"* | D1-14 | D1-14 | phase1 D1-14 |
| the threshold (bare — **superseded** by the re-scope) — anchor: *"the guard's threshold stated, derived and"* | D1-15 | D1-13 | phase1 D1-15 |
| the **single-read guard** (no torn view) — anchor: *"helper single-read concurrency (canonical record: decisions.md"* | D1-16 | D1-12 | decisions D1-12 |
| the **re-scoped guard semantics** (W1) — anchor: *"the final size-guard semantics (leader directive,"* | D1-17 | D1-15 | phase1 D1-17 |

### The `D2-*` cross-file map (blocker 3 — the same class, closed)

`phase2-plan.md` defines **D2-4…D2-15** (the implementation record); `decisions.md` carries a **bundled ruling
register** under **D2-1…D2-10** with **different topics** for the overlapping ids. **Canonical = the phase file**
for anything implemented; the register id is the ruling entry.

| Topic | `phaseN-plan.md` id | `decisions.md` id | **Canonical** |
|-------|---------------------|--------------------|------------------------|
| the layering recommendation — anchor: *"recommendation accepted-by-leader 2026-10-29 (ruling 1): l2a"* | — | D2-1 | decisions D2-1 |
| the budgets — anchor: *"budgets (each justified phase2-plan.md §3.2) accepted"* | — | D2-2 | decisions D2-2 |
| the rejected alternative — anchor: *"the rejected alternative: "just raise the"* | — | D2-3 | decisions D2-3 |
| the `_HALT_JOURNALED` re-entrancy guard — anchor: *"_halt_journaled re-entrancy guard _signal_journal_halt (lib.sh:2046-:2050): the"* | D2-4 | D2-5 (bundled) | phase2 D2-4 |
| the breach order (journal→warn→release→exit) — anchor: *"breach order load-bearing: journal row warn"* | D2-5 | D2-5 (bundled) | phase2 D2-5 |
| the unlocked-path breach split — anchor: *"unlocked-path breach split. rollback.sh:90 restart.sh:133 status.sh:52"* | D2-6 | D2-5 (bundled) | phase2 D2-6 |
| trap-install before `lock_acquire` — anchor: *"install the signal handlers before lock_acquire"* | D2-7 | D2-5 (bundled) | phase2 D2-7 |
| `unit_properties` plumbing + kind-scoping — anchor: *"unit_properties plumbed through spawn_executor and runtimemaxsec"* | D2-8 | D2-6 (bundled) | phase2 D2-8 |
| the derived `RuntimeMaxSec` — anchor: *"runtimemaxsec derived, never constant: max(1200, promote_total_deadline_s"* | D2-9 | D2-6 (bundled) | phase2 D2-9 |
| the stage-tree budget derived + the batching — anchor: *"fix the constant, not the budget:"* | D2-10 | D2-7 | phase2 D2-10 |
| preflight-only arming + the real-soak control — anchor: *"arm/disarm the preflight phase budget around"* | D2-11 | — | phase2 D2-11 |
| the row vocabulary + exit 78 + the two breach classes — anchor: *"row vocabulary: the breach row keeps"* | D2-12 | D2-8 | phase2 D2-12 |
| the cgroup-wide-kill INVARIANT — anchor: *"design invariant (recorded verbatim, per architect"* | D2-13 | D2-4 | phase2 D2-13 |
| the `_run_bounded` wrapper contract — anchor: *"_run_bounded wrapper contract (house primitive, lib.sh:3531-:3557):"* | D2-14 | D2-10 | phase2 D2-14 |
| the 120 s justification — anchor: *"the 120 step-budget justification re-derived: 120"* | D2-15 | — | phase2 D2-15 |
| the normalized breach-row shape (W3) — anchor: *"the breach-row shape normalized; both shapes"* | — (phase2 §3.4b + T2.23 + AC23) | D2-9 | phase2 T2.23 / AC23 |

### The `D3-*` cross-file map (blocker 3 — the same class, closed)

`phase3-plan.md` defines **D3-1…D3-16**; `decisions.md` carries a **bundled register** under **D3-1…D3-7** with
**different topics**. **Canonical = the phase file.**

| Topic | `phaseN-plan.md` id | `decisions.md` id | **Canonical** |
|-------|---------------------|--------------------|------------------------|
| the per-run file sink (single-file era) | D3-1 — **SUPERSEDED** | — | superseded |
| the shell writes the file itself — anchor: *"the shell writes the file itself"* | D3-2 | — | phase3 D3-2 |
| the aggregate pointer (single-file era) | D3-3 — **SUPERSEDED** | — | superseded |
| per-line timestamps — anchor: *"per-line timestamps are added the shell's"* | D3-4 | — | phase3 D3-4 |
| a write failure is reported — anchor: *"write failure reported: when the file"* | D3-5 | — | phase3 D3-5 |
| H1 — the client stderr PIPE — anchor: *"closed redirecting the daemon's client stderr"* | D3-6 | — | phase3 D3-6 |
| H2 — the benign `head -1` EPIPE — anchor: *"closed redirecting _supervision_owned_pids's stderr (2>/dev/null the"* | D3-7 | — | phase3 D3-7 |
| mid-phase markers — anchor: *"mid-phase visibility delivered phase/step marker written"* | D3-8 | — | phase3 D3-8 |
| the run identity journalled — anchor: *"the per-run log path journalled run_id"* | D3-9 | — | phase3 D3-9 |
| the retention rule — anchor: *"retention rule (the plan's gap): owner"* | D3-10 | D3-2 | phase3 D3-10 |
| the atomicity contract — anchor: *"atomicity contract: one log line one"* | D3-11 | D3-3 | phase3 D3-11 |
| the two-file sink — anchor: *"the sink two files per run"* | D3-12 | D3-1 | phase3 D3-12 |
| the in-op markers — anchor: *"in-op progress markers: the step-boundary markers"* | D3-13 | D3-4 | phase3 D3-13 |
| the change surface — anchor: *"change-surface additions (both missed the plan):"* | D3-14 | D3-5 | phase3 D3-14 |
| the env scoping — anchor: *"env scoping: per-run logs live under"* | D3-15 | D3-6 | phase3 D3-15 |
| the T3.10 additions — anchor: *"t3.10 additions: (i) cross-env mixing sandbox"* | D3-16 | D3-7 | phase3 D3-16 |

### The `D4a-*` / `D4b-*` map (blocker 3 — audited: a GAP, not a collision)

`phase4-plan.md` defines **no** decision rows — it **cites** the D4a-*/D4b-* decisions, which live **only** in
`decisions.md` (the register of record). No id collision exists; the gap is recorded so a reader of phase 4 knows
where the decisions live.

| Topic | `phaseN-plan.md` id | `decisions.md` id | **Canonical** |
|-------|---------------------|--------------------|------------------------|
| the framing correction (trap-only release) — anchor: *"the framing correction"* | — (phase4 cites) | D4a-1 | decisions D4a-1 |
| the fix (3 parts) | — (phase4 cites) | D4a-2 | decisions D4a-2 |
| explicitly not done (the policy is unchanged) — anchor: *"explicitly not done"* | — (phase4 cites) | D4a-3 | decisions D4a-3 |
| routed (the pipeline-hygiene question) | — (phase4 cites) | D4a-4 | decisions D4a-4 |
| the routing recommendation — anchor: *"recommendation: not change the kill contract"* | — (phase4 cites) | D4b-1 | decisions D4b-1 |
| what this lane changes (correlatability) — anchor: *"what this lane does change (the"* | — (phase4 cites) | D4b-2 | decisions D4b-2 |
| the ‘if the leader rules fix-now’ contingency — **RESOLVED** — anchor: *"the leader rules "fix now" resolved"* | — (phase4 cites) | D4b-3 | decisions D4b-3 |
| the routed commission's mandatory scope — anchor: *"the routed commission's mandatory scope (three"* | — (phase4 §3.2b + T6.7-style task T4.7 + AC7) | D4b-4 | decisions D4b-4 |

### The `D6-*` map (blocker 3 — audited: ONE id collision, now aligned)

`phase6-plan.md` defines **D6-1…D6-6, D6-10, D6-11**; `decisions.md` defines **D6-1…D6-11**. The ids **D2-…
D6-3** share topics; **D6-4** is the same decision from two sides (the phase file's paragraph is its *sequencing
clause*); **D6-6/D6-7** was a genuine same-topic/different-id collision in the phase file — **the phase file's
seat-correction entry was realigned from D6-7 to D6-6** (a one-id local renumber, recorded here as the mapping
line: *old phase6 D6-7 → D6-6*). **Canonical = `decisions.md`** for this series.

| Topic | `phaseN-plan.md` id | `decisions.md` id | **Canonical** |
|-------|---------------------|--------------------|------------------------|
| the deployment-truth chain — anchor: *"decision: the chain above hard precondition"* | D6-1 | D6-1 | decisions D6-1 |
| the user-owned sync precondition — anchor: *"decision: this lane cannot and will"* | D6-2 | D6-2 | decisions D6-2 |
| the arm-time provenance — anchor: *"decision: pure observability, zero refusal risk"* | D6-3 | D6-3 | decisions D6-3 |
| the stamp + the arm-time acquisition + the floor check (phase6's D6-4 is its **sequencing clause**) — anchor: *"decision: the refusal fires before the"* | D6-4 | D6-4 | decisions D6-4 + phase6 §4.2 |
| the watchdog token — anchor: *"decision: the token belt, not the"* | D6-5 | D6-5 | decisions D6-5 |
| the guard's seat is the DAEMON — anchor: *"decision: the guard's seat the daemon,"* | D6-6 | D6-6 | decisions D6-6 |
| the runbook checklist item — anchor: *"runbook checklist item [fold: a31]"* | — (phase6 task T6.7) | D6-7 | phase6 T6.7 |
| the stamp cadence (leader-ratified) — anchor: *"stamp cadence leader-ratified 2026-10-29"* | — | D6-8 | decisions D6-8 |
| rollout / efficacy — anchor: *"rollout efficacy (conditional, and stated such)"* | — (phase6 task T6.8) | D6-9 | phase6 T6.8 |
| the Q1 hybrid floor — anchor: *"decision (leader ruling q1, 2026-10-29 final):"* | D6-10 | D6-10 | decisions D6-10 |
| the Q2 warning-only posture — anchor: *"decision (leader ruling q2, 2026-10-29 final):"* | D6-11 | D6-11 | decisions D6-11 |

### The mutation matrix (W2 extension)

The architect fold added **M22–M24** (Pack I); the council fold added **M25–M28** (M25 the **stamp-acquisition** leg — the stamp must come from the file, not from
the executor; the **mixed-generation token refusal** is **M32**, added round 5 — approver-002 **N-15**; M26 non-ancestor pinned dir ⇒ RED; M27 missing floor object ⇒ RED;
M28 non-git branch must warn, not refuse); the round-4 fold adds **M29–M31** (Pack J: M29 the guard refuses on
`rollback.sh` ⇒ RED; M30 the threshold is consulted on the **O(N)** path ⇒ RED; M31 the legacy-class conjunct is
dropped ⇒ RED). ⇒ the canonical span is **M1–M32**, stated identically in every file and asserted by the
**count-sweep gate** (class (b) — the span rule).

## 12. Amendment fold log (architect → plan set, 2026-10-29)

All **35** amendments from `architecture-recommendation.md` §5 are folded. Per-amendment status:
**folded** = new content added · **already-present** = the existing text already satisfied it · **superseded** = a
later ruling/amendment overrides it. Detailed per-amendment confirmation is in the report; the per-file fold logs
live at the end of each phase file.

| A# | Subject | Status | Folded into |
|----|---------|--------|-------------|
| A1 | `_HALT_JOURNALED` re-entrancy guard + double-TERM pin | folded | `phase2-plan.md` D2-4, T2.12, AC12 |
| A2 | Breach order journal→warn→release→exit + live-shaped-path test | folded | `phase2-plan.md` D2-5, T2.13, AC13 |
| A3 | Unlocked-path breach split; `status.sh` exits 1; AC2 amended | folded | `phase2-plan.md` D2-6, T2.14, AC14; `plan-overview.md` SC4b |
| A4 | Trap handlers installed BEFORE `lock_acquire` | folded | `phase2-plan.md` D2-7, T2.15, AC15 |
| A5 | `unit_properties` plumbing; RuntimeMaxSec only for `kind=="promote"` | folded | `phase2-plan.md` D2-8, T2.16, AC16 |
| A6 | RuntimeMaxSec **derived**; refuse `SOAK_S>300` in live mode | folded | `phase2-plan.md` D2-9, T2.17, AC17 |
| A7 | Batched `_tree_manifest` + byte-parity pin; stage budget **derived** from the re-measured plugins walk | folded | `phase2-plan.md` **D2-10, T2.18**, AC18; `phase0-plan.md` T0.13 |
| A8 | Arm/disarm the preflight budget around the preflight only; REAL 300 s conservative soak negative control | folded | `phase2-plan.md` **D2-11, T2.19**, AC19; `plan-overview.md` SC5 |
| A9 | Row vocabulary (`halt` + `deadline-breach-<step> …`), exit 78, two breach classes + `RuntimeMaxUSec` runbook step | folded | `phase2-plan.md` D2-12, T2.20, AC20 |
| A10 | Phase-0 runtime probe of the unit's kill properties; cgroup-wide-kill INVARIANT verbatim | folded | `phase0-plan.md` T0.11, AC11; `phase2-plan.md` D2-13, AC21 |
| A11 | `_run_bounded` wrapper contract (side-effect-free or process-group kill; never on write paths; child `halt` tolerance; 137/143→124) | folded | `phase2-plan.md` D2-14, T2.21, AC22 |
| A12 | Budget justification re-derived (120 s ≈ 2.3× the longest single step) | folded | `phase2-plan.md` §3.2 + D2-15 |
| A13 | Phase-0 measured baselines (fork cost, plugins double-walk, agents+frontend walk) | folded | `phase0-plan.md` T0.12, AC12 |
| A14 | Helper stdout contract = **bash command-substitution emulation** | folded | `phase1-plan.md` D1-10, T1.11, AC12; `plan-overview.md` SC2 |
| A15 | Corpus 15→22 + guard-boundary trio + duplicate-key BASE-bug-preserving pins | folded | `phase1-plan.md` §3.3 + D1-11, T1.12, AC13; `phase0-plan.md` T0.9 |
| A16 | Three-algorithms rule; forbid sharing `in_str` across verbs; both `_json_sub` quadratic mechanisms | folded | `phase1-plan.md` D1-12, T1.13, AC14 |
| A17 | Differential harness (true-BASE oracle; Option-D proxy only after a validation leg) | folded | `phase0-plan.md` T0.10, AC10; `phase5-plan.md` Pack B |
| A18 | Census gains failure-mode-class + stderr-contract columns; no-`set -e`; rc-preservation pins | folded | `phase0-plan.md` §4.2, T0.14, AC13 |
| A19 | Forbid routing the read helper through `_splice_journal`; placement = new `scripts/upgrade/journal_read.py` | folded | `phase1-plan.md` **D1-13**, §3.1, AC15 |
| A20 | Log retention rule (keep-20 / 14 d, portable primitives, five fences) | folded | `phase3-plan.md` D3-10, T3.11, AC11 |
| A21 | Atomicity contract (4 KiB cap, elision, newline sanitization; no per-line fsync) | folded | `phase3-plan.md` D3-11, T3.12, AC12 |
| A22 | **Two-file sink**; pointer names both files by run_id; AC1 scoped to successful writes; AC2 asserts journald visibility | folded | `phase3-plan.md` D3-12, T3.13, AC13 (supersedes the single-file sink of D3-1/D3-3) |
| A23 | In-op markers every 500 files; NO background heartbeat subshell | folded | `phase3-plan.md` D3-13, T3.14, AC14 |
| A24 | Change surface: sweep per-run tail via run_id; env-scoped mkdir; reuse `_log_tsl`; fix `lib.sh:358-360` | folded | `phase3-plan.md` D3-14, T3.15, AC15 |
| A25 | T3.10 additions (no cross-env mixing; no secrets in pointer rows; the ≥8 KiB corruption pin) | folded | `phase3-plan.md` T3.16, AC16 |
| A26 | The numbered deployment-truth table | folded | `phase6-plan.md` §2, D6-1 |
| A27 | The hard user-owned sync precondition | folded | `phase6-plan.md` §3, D6-2, T6.2 |
| A28 | Daemon-seat provenance at arm time (scripts_dir, lib.sh sha256, git rev+dirty) | folded | `phase6-plan.md` D6-3, T6.3 |
| A29 | `PIPELINE_SCRIPTS_REV` stamp + daemon floor check **before `consume_pending_action`** | folded | `phase6-plan.md` D6-4, T6.4, T6.5, AC4 |
| A30 | `ENSEMBLE_PROMOTE_WATCHDOG=1` token + mixed-generation refusal | folded | `phase6-plan.md` D6-5, T6.6 |
| A31 | Runbook checklist item (arm row shows the expected stamp) | folded | `phase6-plan.md` T6.7, AC7 |
| A32 | §9 assumptions: A4 gains the derived-RuntimeMaxSec clause; add A6 | folded | `plan-overview.md` §9 (A4a, A6) |
| A33 | §5 risk table: append the architect's R-A…R-I rows | folded | `plan-overview.md` §5.1 |
| A34 | §6 SC list: add SC4b + SC12 | folded | `plan-overview.md` §6 (SC4b, SC12) |
| A35 | D4b routed board item gains the three mandatory scope items | folded | **`decisions.md` D4b-4** (the definition) · `phase4-plan.md` T4.7 / AC7 (the implementation row) |

---

## 13. Council fold log (plan council round 3 → plan set, 2026-10-29)

**Verdict:** ⚠️ APPROVE-WITH-ADVISORIES, **conditional** on this one pre-implementation fold pass (phases 0–5
approved as verified; all three blockers sat in phase-6 / post-fold material). Source:
`review-plan-council.md` (READ-ONLY; not modified).

| Finding | Subject | Status | Folded into |
|---------|---------|--------|-------------|
| **B1** | The arm-time floor comparison was undefined (git SHAs are unordered; no ordering mechanism existed) + the non-git pinned-dir case was undefined | **folded** | `phase6-plan.md` §3 (the "≥" predicate → ancestry) · §4.2 · D6-10 · T6.9 · T6.11 · AC9 · Pack I legs (M26–M28) · `decisions.md` D6-10/D6-11 |
| **B2** | Mutually exclusive `RuntimeMaxSec` constants across the pins/ACs (the stale literal `1200` survived in 9 places) | **folded** | `phase2-plan.md` (ladder row, T2.8, AC8) · `phase5-plan.md` (Pack H, T5.9, AC9) · `decisions.md` (L3 row, ladder row, D2-2 table) — the sweep result is in the lane report; a **grep-assertion** is now a pin (`phase5-plan.md` T5.17 / AC17) |
| **B3** | The guard's only stated input acquisition (the executor-start echo) arrives **after** the nonce burn ⇒ it cannot gate anything | **folded** | `phase6-plan.md` §4.2 (the acquisition row) · T6.10 · AC10 · Pack I leg **M25** · `decisions.md` D6-4 |
| **W1** | The budget-scope table contradicted the scope paragraph, and `retention_evict` (a **write** path) was listed as armable | **folded** | `phase2-plan.md` §3.3a / §3.3b (split + *side-effecting?* column + **MUST-NOT-WRAP**) · §3.6 (the deliberately-uncovered commit-phase sites) · `plan-overview.md` R25 |
| **W2** | The size guard could block the **emergency rollback** path; the threshold was underived | **folded — DISCHARGED by leader ruling Q3** | `phase1-plan.md` D1-6 (path scope amended) · D1-14 · D1-15 · T1.15 · AC16 · `plan-overview.md` SC13 + R23 + §8 (Adv6 disclosure) |
| **W3** | Two breach producers emit different row shapes (`daemon-state=` present in `_enforce_deadline`, absent in `_budget_breach`) | **folded** | `phase2-plan.md` §3.4b · T2.23 · AC23 |
| **W4** | Stale-count / single-file-era drift cluster + no count-sweep gate | **folded — the count-sweep gate was RUN by the planner on 2026-10-29: PASS, 0 failures across 6 classes (result quoted in `phase5-plan.md` §9)** — ⚠ **SUPERSESSION RECORD**: the cell's literals are the **pre-round-4** values (`M1–M28` / `A–I`); the **canonical** values are **`M1–M32`** / **`A–J`** (updated in this cell). This cell is a **historical record**, exempt from the gate's class (b)/(c) by the exemption stated here. **[fold: N1]** | Corpus **22 + G1–G5 = 27** everywhere (`plan-overview.md` R2/SC2 · `phase0-plan.md` T0.2/AC2/T0.10 · `phase1-plan.md` T1.1/AC1 · `phase5-plan.md` Pack A/T5.2/AC2) · matrix **M1–M32** (`phase5-plan.md` T5.10/AC10/fold log) · packs **A–J** (`phase5-plan.md` gate row + fold log) · phase-3 single-file wording retired (`phase3-plan.md` O1, change surface, T3.1, AC1/AC4/AC8/AC9) · anchor fix `lib.sh:5567` (`phase3-plan.md` D3-10) · **count-sweep gate added** (`phase5-plan.md` T5.17/AC17 + `plan-overview.md` §3 gate chain) · **cross-file ID index** (§11.1) |
| **W5** | The staged verification cannot see the shell surface (payloads ship no `scripts/` since v0.27.4) | **folded** | `phase6-plan.md` T6.13 · AC13 · `phase5-plan.md` Pack I (the split) · `plan-overview.md` SC14 |
| **W6** | `_run_bounded` blast radius: the consumer census is **7**, not 5; the modified-primitive-vs-new-variant choice was unstated; `setsid` portability was unstated | **folded** | `phase2-plan.md` §3.4b (the 7-consumer predicate table + the **new-variant** decision + the `setsid` fallback) · T2.24 · AC24 |
| **Adv1** | M25 mutation leg (the **stamp-acquisition** leg — *not* the token refusal, which is **M32**) | **folded** | `phase5-plan.md` Pack I (M25 = stamp-from-file ⇒ RED; **M32** = `promote.sh` ignores the watchdog token ⇒ RED) · `phase5-plan.md` T5.10/AC10 (matrix span) — ⚠ **SUPERSESSION RECORD**: the literal `M1–M28` in this cell is the **pre-round-4** value; the **canonical** span is **`M1–M32`** (the M29–M31 Pack J legs were added in round 4). Exempt from the gate's class (b) by the exemption stated here. **[fold: N1]** |
| **Adv2** | `ENSEMBLE_ALLOW_STALE_SCRIPTS` must be **per-invocation only**, never persisted | **folded** | `phase6-plan.md` §4.2 (escape-hatch row) · T6.12 · AC12 |
| **Adv3** | phase1 case-15 verdict cell → "accept (BASE)" | **folded** | `phase1-plan.md` §3.3 case 15 |
| **Adv4** | Helper single-read concurrency leg (no torn view under an atomic replace) | **folded** | `phase1-plan.md` D1-16 · T1.16 · AC17 |
| **Adv5** | `_HALT_JOURNALED` post-completion TERM semantics (first-row-wins, idempotent) | **folded** | `phase2-plan.md` T2.12 · AC12 |
| **Adv6** | Guard × journal-growth-fix coupling disclosure | **folded** | `plan-overview.md` §8.6 (stage adjudication + promote-time notes) |
| **Adv7** | Size-guard × fallback ordering matrix cell | **folded** | `phase1-plan.md` T1.17 · AC18 |
| **Q1** | The leader's ruling: HYBRID, primary = GIT ANCESTRY | **folded — VERBATIM** | `phase6-plan.md` §4.2 · D6-10 · T6.9 · AC9 · `plan-overview.md` §11.3 |
| **Q2** | The leader's ruling: a NON-git pinned dir is **WARNING-ONLY** | **folded — VERBATIM** | `phase6-plan.md` §4.2 · D6-11 · T6.11 · AC11 · `plan-overview.md` §11.3 |
| **Q3** | The leader's ruling: the size guard refuses on the **PROMOTE PATH ONLY** (rollback/restart/status get WARN+continue) | **folded — VERBATIM** | `phase1-plan.md` D1-14 · T1.15 · AC16 · `plan-overview.md` SC13 + R23 + §8 |
| **Standing rule** | The sibling-lane (`wi-3f5bea95`) "zero overlap" record is as-of 2026-10-29 against a base-sitting worktree ⇒ re-verify at implementation time | **folded** | `phase6-plan.md` T6.14 · AC14 |

### The three leader rulings, recorded verbatim

> **Q1 (B1 floor mechanism) — RULED: HYBRID, primary = GIT ANCESTRY.** The daemon records
> `PIPELINE_SCRIPTS_FLOOR_COMMIT` = the commit introducing the preflight-hang fix (owner: this lane's release
> process; storage: daemon constants + release manifest; cadence: bumps per release touching ceremony-relevant
> scripts/upgrade semantics — SAME cadence as the ratified `PIPELINE_SCRIPTS_REV` stamp, they travel together).
> At arm time, for a git pinned dir: read-only bounded `git -C <dir> merge-base --is-ancestor <floor> HEAD` —
> pass iff ancestor; a missing/unknown floor object or command error = REFUSE (a tree that does not contain the
> fix commit object cannot contain the fix; cherry-picked-content edge = acceptable rare false refusal,
> remediation is a proper fetch/merge). Fallback for NON-git pinned dirs = stamp file when present
> (`PIPELINE_SCRIPTS_REV`), else WARN-only per Q2. Rationale: ancestry gives no false refusals from newer
> unrelated trees (any tree containing the fix passes, however new) and proves staleness exactly; a bare sha256
> allow-list cannot prove staleness of an unknown sha. **If you find ancestry impractical at the exact call site,
> the content-sha allow-list is the approved fallback — say so explicitly if you take it.**

> **Q2 (non-git pinned dir posture) — RULED: WARNING-ONLY** (council recommendation confirmed). WARN + journal
> row carrying the pinned lib.sh sha256 as provenance; never refuse on the non-git branch. Rationale: every known
> deployment pins a git dev checkout; refusing an operator-chosen non-git dir could block an otherwise-correct
> promote, and the sha256 journal row preserves the audit trail.

> **Q3 (size-guard exemption, W2) — RULED: PROMOTE-PATH-ONLY refusal.** rollback.sh / restart.sh / status.sh get
> WARN + continue. Rationale: the emergency rollback path must never be blocked by a guard added for promote
> hygiene — blocking recovery turns degradation into no-recovery. Add the runbook step. W2 is DISCHARGED by this
> ruling once folded.

**Explicit statement required by Q1:** ancestry **IS** practical at the exact arm call site (the daemon already
runs in-process there; the check is read-only, bounded, and writes nothing) ⇒ **the content-sha allow-list
fallback is NOT taken.** The two accepted false-refusal edges (cherry-pick, shallow clone) are recorded in R24.

---

## 14. Round-4 doc-sweep fold log (2026-10-29)

Source: the appended **delta re-check** in `review-plan-council.md` (READ-ONLY; not modified). Verdict:
✅ **CLEAR-WITH-NOTES** — B1/B2/B3 discharged at mechanism level; the mutation-matrix extension ACCEPTED
(the architect+ council legs **M22–M28**; since extended to **M1–M32** by the round-4 Pack J legs); 10 residuals
folded here.

| Residual | Subject | Status | Folded into |
|----------|---------|--------|-------------|
| **W1** | The size-guard semantics were self-contradictory (D1-15 "an order of magnitude below 455 KB" vs T1.6 "a 100 KB fixture does not trip" vs an implied 600 KB that would **not** have fired on the 455,783 B incident journal) | **folded — RE-SCOPED** (leader directive) | **phase1 D1-17** (the final semantics) · D1-15 marked superseded · T1.6 amended · **T1.18** · AC7 amended · **AC19** (the boundary trio) · §3.3 G1–G5 · **phase5 Pack J** · `decisions.md` **D1-15** · **SC13** · **R26** · §8.6 (updated) |
| **W2** | The Q3 path scope had **no named pack** — a rollback-refusing regression had zero RED legs | **folded** | **phase5 Pack J** (legs **J1–J7**; mutations **M29–M31**) · `plan-overview.md` SC13 (measurement column) · phase6 fold-log pointer (A1) |
| **W3** | The superseded constant `MIN_PIPELINE_SCRIPTS_REV` survived at `phase6-plan.md` T6.4 and `decisions.md` D6-8 | **folded** | `phase6-plan.md` **T6.4** · `decisions.md` **D6-8** — both now name **`PIPELINE_SCRIPTS_FLOOR_COMMIT`**; the count-sweep gained class **(g)** (superseded identifiers) so this class cannot recur |
| **W4** | The invalid "≥ this lane's merge" ordering predicate survived at 4 **operator-facing** prose sites | **folded** | `phase6-plan.md` §1 (i) + §3 (b) · `plan-overview.md` §9 A6 + §11 — all now state the **ancestry** predicate (`git -C <dir> merge-base --is-ancestor <floor> HEAD` passes) |
| **W5** | The cross-file `D1-*` id collision (phase1 vs decisions) was load-bearing | **folded** | `plan-overview.md` §11.1 — a per-ID **topic column** + the **`D1-*` map**; `decisions.md` D1-12 ↔ D1-14 **swapped** with a **mapping table**; canonical citations: **Q3 = phase1 D1-14**, **single-read = decisions.md D1-12** |
| **A1** | False "Pack A matrix cell" pointers | **folded** | `phase6-plan.md` §8 fold log · `plan-overview.md` **SC13** → `phase1-plan.md` T1.17/AC18 + `phase5-plan.md` Pack J; `phase1-plan.md` T1.17's touchpoint now names Pack J |
| **A2** | The count-sweep rule (5 classes) was narrower than its own result record (6 classes) | **folded** | `phase5-plan.md` **T5.17** → **seven** classes (incl. the anchor class (f) and the span rule) · **AC17** aligned |
| **A3** | No gate class for superseded identifiers (the W3 residue survived because of it) | **folded** | `phase5-plan.md` T5.17 class **(g)** · AC17 |
| **A4** | T6.5's pre-B1 vocabulary ("absent-or-older-than-floor") | **folded** | `phase6-plan.md` **T6.5** — now states the §4.2 comparison (ancestry / missing floor object / unparsable stamp) |
| **A5** | AC-table ordering (AC8 listed last) | **folded** | `phase5-plan.md` (AC17 after AC16) · `phase6-plan.md` (AC8 before AC9) — both tables are now monotonic |

### Count-sweep gate — RE-RUN RESULT (round 4)

**PASS — 0 failures across EIGHT classes (+1 positive).** Classes: **(a)** corpus **22 + G1–G5 = 27**; **(b)** the matrix **span
rule** (every live span claim is **M1–M32**); **(c)** the pack set **A–J**; **(d)** no live
`RuntimeMaxSec=1200`; **(e)** no single-file-era wording; **(f)** the anchor class (`lib.sh:5569` gone); **(g)** the
superseded-identifier class (no live `MIN_PIPELINE_SCRIPTS_REV`; `PIPELINE_SCRIPTS_FLOOR_COMMIT` present). The run
caught two real residues (the historical `M1–M28` span mention in §14 and the index row) — both fixed — and
re-verified classes (f)/(g), which is exactly what class (g) was added to catch.

### The W1 final semantics (the number + the trip condition)

* **Name:** `JOURNAL_SIZE_GUARD_BYTES` · **Default: 98304 (96 KiB)** · env-overridable.
* **Trip condition = TWO CONJUNCTS:** (1) the read is about to execute the **legacy-class per-character bash
  walk** (the python helper absent/unusable ⇒ the fallback path), **AND** (2) the journal size ≥ the threshold.
* **On the healthy O(N) python path the guard is NOT CONSULTED** ⇒ reads proceed at **any** size.
* **Q3 path scope intact:** promote ⇒ **REFUSE loudly** (size-named row + non-zero rc); `rollback.sh` /
  `restart.sh` / `status.sh` ⇒ **WARN + continue**.
* **Boundary trio (each on the legacy class):** **G1** threshold−1 ⇒ **allowed** · **G2** exactly the threshold ⇒
  **REFUSE** · **G3** threshold+1 ⇒ **REFUSE** · **G4** the **O(N)** path at threshold+1 **and** at 455,783 B ⇒
  **NO refusal** (the next-ceremony protection).
* **Why 98304:** the measured legacy-walk curve (11 KB → 2.27 s · 55 KB → 51.6 s · 111 KB → 199 s) crosses the
  **120 s preflight step budget** at ≈ **87 KB** ⇒ 96 KiB sits just above the crossover: below it the legacy walk is
  inside the step budget (allowed); at/above it the walk would blow the budget anyway (refuse loudly rather than
  trip a vague budget). The **incident configuration** (455,783 B on the legacy walk) exceeds it ⇒ (a) holds; the
  **live 455 KB+ journal on the O(N) path** never consults it ⇒ (b) holds.

---

## 15. Approver round-5 fold log (2026-10-29)

**Verdict:** approver iteration 001 **REJECTED** — 4 blocking issues, all mechanical text corrections against
artifacts already in the set; **no design change**. Folded here; iteration 002 re-approves.

| Issue | Subject | Status | Folded into |
|-------|---------|--------|-------------|
| **Blocker 1** | §5.1 "Encoded in": 9 of 11 phase2/phase3 pointers stale or dangling | **folded** | R12 → `phase2-plan.md` **D2-10 / T2.18** · R16 → `phase2-plan.md` **D2-8** · R17 → `phase2-plan.md` **D2-9 / T2.17** · R19 → `phase2-plan.md` **D2-14 / T2.21** · R20 → `phase2-plan.md` **D2-7 / T2.15** (the dangling `D2-3` id is gone) · R21 → `phase2-plan.md` **D2-14** · R22 → `phase2-plan.md` **D2-12 / T2.20** · R-E + R-H → `phase3-plan.md` **D3-14 / T3.15** · R15 → `phase2-plan.md` **D2-5 / T2.13 (A2)** + `phase2-plan.md` **D2-6 / T2.14 (A3)** · R-G extended with `phase6-plan.md` **T6.5** · R1 → `phase2-plan.md` **D2-11 / T2.19** · R8 → `phase2-plan.md` **D2-9 / T2.17** · R25 → `phase2-plan.md` **§3.3a / §3.3b + D2-14 / T2.24** |
| **Blocker 2** | The A7/A8 fold-log swap between §12 and `phase2-plan.md` §6 | **folded** | the **phase file was correct** (A7 = batching + the derived stage budget ⇒ D2-10 + **T2.18**; A8 = preflight-only arming + the real soak ⇒ D2-11 + **T2.19**) ⇒ `plan-overview.md` §12 corrected; both rows now agree |
| **Blocker 3** | Unresolved ID-series collisions: §11.1 asserted D2-1…D2-15 while the phase file defines D2-4…D2-15; D3-* unmapped | **folded** | §11.1 ranges corrected to the **true** sets (phase2 **D2-4…D2-15**; phase3 **D3-1…D3-16**; phase4 **none**; phase6 **D6-1…D6-6, D6-10, D6-11**); **four cross-file maps added** (D2-*, D3-*, D4a/D4b-*, D6-*) with a per-topic **canonical** column; the one real D6 collision (**phase6 D6-7 ⇒ D6-6**, the seat correction) **realigned** in the phase file with the mapping recorded |
| **Blocker 4** | The drift gate had no class asserting that a cited D-id/T-id resolves to its subject | **folded** | `phase5-plan.md` **T5.17 / AC17** → **EIGHT** classes, the new **(h) RESOLUTION class**; the gate was **RUN** (§16) |
| **N1** | §13 W4/Adv1 rows carry pre-round-4 literals (`M1–M28`, `A–I`) | **folded** | both cells carry an explicit **SUPERSESSION RECORD** stating the historical literal, the canonical value (**M1–M32** / **A–J**) and the gate exemption |
| **N2** | Dotted-vs-hyphenated task-id drift | **folded** | the **dotted** form is canonical (5 of 7 phase files) ⇒ `phase3-plan.md`'s 26 hyphenated ids normalised to `T3.1…T3.16`; 4 citation sites in 2 other files swept; the choice is stated in §11.1 |
| **N3** | §1's D4(a) one-liner was stale | **folded** | §1's D4 row now states the **corrected framing** (trap-only release ⇒ a SIGKILL-class death orphans the lock dir + the **unjournaled reclaim**; a normal successful stage **does** release the lock) and points at `phase4-plan.md` §2.1 |
| **N4** | The sandbox-fence contract was split between phase0 P0-D2 and phase5 §3 | **folded** | `phase5-plan.md` **§3 is the ONE canonical statement** (declared inline); `phase0-plan.md` T0.7 + AC7 now **cross-reference** it and do not restate it |
| **N5** | phase2's task table listed T2.22 after T2.24 | **folded** | task rows re-ordered into numeric order in **phase2** (T2.22) and, for consistency, in **phase1** (T1.17/T1.18), **phase5** (T5.16/T5.17) and **phase6** (T6.8/T6.9) — **no renumber** |

---

## 16. The count-sweep gate — FINAL RUN (2026-10-29, fold round 6, after the approver fold)

**The gate is an EXECUTABLE ARTIFACT, not a claim**: `gate.sh` (in this directory, mode `755`) — run it with
`bash .agents/shared/planning/promote-preflight-hang/gate.sh` (`--quiet` for one line per class). It reads the nine
plan documents **only**, never executes a pipeline script, never touches the live install, never resolves a
live-shaped path, is read-only/idempotent, and **exits non-zero on any failure**. **[fold: approver-002 N-19 —
a recorded result with no named artifact cannot be re-verified]**

**RESULT: PASS — exit 0, 0 failures across 12 executable checks** = the EIGHT classes (a)–(h) + the (g+) positive
assertion + the (i) artifact/count check (which asserts this very number against the gate, so the recorded result
cannot drift — N-16). Class (h) carries three sub-assertions: **h1** file-qualified resolution, **h2**
every-id-defined-somewhere, **h3** map-row **TOPIC** resolution. The classes and the rule set are in
`phase5-plan.md` T5.17/AC17; the verbatim run + the mutation-proof is recorded in `phase5-plan.md` §9 and captured as an
artifact at `evidence/gate-run-round6.txt` (the output, the exit code, and the twelve planted-defect REDs).

**What class (h) caught on its first run (round 5, pre-fix): 13 unresolved file-qualified citations + 18 ids defined
nowhere** — the approver's blockers 1–3 in one number: the nine stale §5.1 pointers, the dangling `D2-3` id, the
`phase2-plan.md` **`D2-3`** citations in R1/R8, the hyphenated-vs-dotted phase3 task ids, the shorthand file refs
(`phase0`, `phase5`, …) that made the §11.1/§13/§14 cells mis-attribute their ids, and four cells whose ids sat after
a *different* file ref. All corrected.

**Class (h) asserts SUBJECT, not mere existence — and it does not exempt the rows it validates.** **(h1)** every
file-qualified citation (`` `phaseN-plan.md` D… `` / `` T… ``) resolves to a row **defined in that file**; **(h2)**
every id **mentioned anywhere** is defined **somewhere** in the set; **(h3)** every **§11.1 cross-file map row** — the
rows that were wrong in blocker 1 — must itself resolve: its cited id must exist **and** its **topic anchor must match
the DEFINING row's topic** (61 rows evaluated, counted rather than assumed). There is **no** map-row exemption — an
earlier draft exempted exactly those rows, which is why blocker 1's defects passed the gate (approver-002 blocker 2).
The only exemptions are **quoted meta-mentions** and **supersession records stated at the cell**.

**Mutation-tested, one planted defect per class (round 6, throwaway copies):** (a) `22 + 3` / `**25** fixtures ⇒ RED ·
(b) live `M1–M28` ⇒ RED · (c) live `packs A–I` ⇒ RED · (d) `RuntimeMaxSec=1200` ⇒ RED · (e) `one per-run file` ⇒ RED ·
(f) `lib.sh:5569` ⇒ RED · (g) `MIN_PIPELINE_SCRIPTS_REV` ⇒ RED · (h1) a real citation pointed at the wrong phase file ⇒
RED · (h2) an undefined `D7-1` ⇒ RED · (h3) a map row re-anchored to another row's topic ⇒ RED. Restored ⇒ GREEN.

---

## 17. Explicit deferrals (implementation-time or routed — nothing here is silent)

**Rule applied:** anything this plan set does *not* do itself is listed here with a rationale and the home that owns
it. An undocumented deferral is treated as blocking (commission item E).

| # | Deferred item | Why it cannot be done in the plan | Home that owns it |
|---|---------------|-----------------------------------|-------------------|
| D-F1 | **The writer-side journal bloat** (2,775 `mcp_server_state` entries ≈ 89 % of the journal) | daemon-side code (`daemon/mcp/init_guard.py:150`/`:561`) with a different owner and different semantics (state transitions, not history); folding it in would exceed this lane's blast radius. The interim mechanism **ships here** (the D1-17 size guard). | `decisions.md` D1-8 · `phase1-plan.md` D1-8 · the MCP/init-guard owner |
| D-F2 | **The benign-detach kill contract** (`wc` / `pkill` on a detached run) | a behaviour change on a kill path with its own risk envelope; the leader CONFIRMED the routing (RULING 3) and froze three **mandatory** scope items for the follow-up. | `decisions.md` D4b-1…**D4b-4** · `phase4-plan.md` §2.3 · T4.7 / AC7 |
| D-F3 | **The stale-break residue** (`lock_acquire` moves the dir to `${lock}.stale.$$` and never removes it — one exists in the live install today) | residue *hygiene*, not correctness, and outside D4(a)'s three parts; deleting a lock dir is exactly the live-install-deletion class the fence forbids. | `phase4-plan.md` §2.2b (explicitly ROUTED) · the lock-protocol owner |
| D-F4 | **Anchor re-verification against the implementation rev** (every `file:line` incl. the four daemon-side anchors) | the rev does not exist yet; a hand-maintained dual column would drift (this is also N-1's remedy (B), which is therefore **not** taken). | `phase0-plan.md` **P0-D4 / T0.1 / AC1** — a **script**, at implementation time |
| D-F5 | **The gate's scope boundary**: `gate.sh` is a *document-consistency* gate | it must never read the code tree or run a pipeline script (safety fence); anchor **freshness** and the daemon-side anchors are a different check with a different safe mechanism. | `phase0-plan.md` T0.1 (anchors) + `phase5-plan.md` T5.17 (`gate.sh`, documents only) |
| D-F6 | **The sibling-lane overlap verdict** (`wi-3f5bea95`) | the "zero overlap" record is dated 2026-10-29 against a base-sitting worktree; overlap must be re-derived when both lanes have moved. | `phase6-plan.md` **T6.14 / AC14** (recorded in the stage adjudication) |
| D-F7 | **`PIPELINE_SCRIPTS_FLOOR_COMMIT`'s value** | the fix commit does not exist until this lane merges; the *mechanism* (set + validate) ships here, the *value* is written at release time and **fails the release** if invalid. | `phase6-plan.md` **T6.15 / AC15** · `phase5-plan.md` §6 |
| D-F8 | **The `architecture-recommendation.md` "15 dirty files" literal** (N-23) | that file is a **READ-ONLY input** (sha-pinned `2ab9d5e1…`); the mutable plan documents carry the time-dependent correction instead. | `phase6-plan.md` fact 4 / T6.2 · `decisions.md` D6-2 · §11 of this file |

---

## 18. Round-6 fold log (approver iteration 002 → 2026-10-29, audit + completion pass)

Round 6 was an **audit-and-completion** pass over a fold whose edits landed unverified (the previous worker died at an
upstream rate limit). Every item below was re-checked against the plan set **on disk** and, where mechanically
checkable, against the executable gate.

| Approver-002 item | Verdict this round | Where |
|---|---|---|
| **B1** §11.1 map resolves to correct topics + two narrative pointers | **verified present, two residuals completed by me** — rows now `D1-10` (stdout), `D1-11` (corpus), `—`/`decisions D1-4` (strict-JSON), `D1-13` (placement); §9 OQ2 + §12 A19 cite `D1-13`; the residual was the **false attribution of `D4b-4`** to `phase4-plan.md` (two cells — that file defines no decision rows) ⇒ both re-pointed to `decisions.md` D4b-4 | §11.1 · §9 · §12 · `decisions.md` (map + corrected claim) |
| **B2** class-(h) reproducibility; `D4b-4` undefined; live `M1–M28`/`A–I` span; map-row exemption | **completed by me** — `D4b-4` **is** defined (`decisions.md:384`), the two citations re-pointed; the span claim carries a **SUPERSESSION RECORD** at the cell; class (h) extended to **subject** resolution (h3) with the **map-row exemption removed**; the gate is now reproduced from `gate.sh` with its output pasted | §16 · `phase5` T5.17/AC17/§9 · `gate.sh` · `decisions.md` W4 cell |
| **B3** four §5.1 rows missing the 5th cell | **verified present** — R-I/R23/R26/R24 all carry a file-qualified "Encoded in" cell | §5.1 L165–168 |
| **B4** Phase 1 verdict-input domain + NUL-adjacent-to-backslash row | **verified present** — D1-1/T1.1 compute over the `$(cat)`-processed bytes **and** reproduce the EMPTY pre-check; corpus row **G5** pins the NUL-adjacent-to-backslash divergence | `phase1` D1-1 · T1.1 · §3.3 G5 |
| **B5** armed-window table misclassified five sites | **verified present** — `:1406`/`:1560`/`:1582` re-bucketed **IN** (via `promote_entry_check` at `promote.sh:245`); `:1803`/`:1876` moved to an explicit **adopt-path** row; §3.6 restated against the corrected map — **residual completed by me: AC3** still said "every site" and was re-stated against §3.3a + the deliberate §3.3b exclusions | `phase2` §3.3a/§3.3b/§3.6 · **AC3** |
| **B6** `adopt-unit.sh` (+`stage.sh`) unlisted interpreter dependency | **verified present in D1-9/T1.7/AC8** — **completed by me**: I6 amended to name all three callers, and the phase-0 §4.2 companion gained the **transitive adopt-path row** (the direct-call census stays exactly 25) | `phase1` I6/D1-9/T1.7/AC8 · `phase0` §4.2 |
| **B7** corpus cardinality stated four ways; G4 uncounted | **verified present** — `22 + G1–G5 = 27` (G5 = the new blocker-4 row); AC10 corrected — **residual completed by me: T0.10** still said "22-case + 3 guard-row" / "**25** fixtures" ⇒ corrected, and class (a) extended to catch those exact shapes | `phase0` T0.2/T0.9/T0.10/AC2/AC9/**AC10** · class (a) |
| **B8** T3.4 = single-file sink | **verified present in T3.4's task text and both §3 rows** — **residual completed by me: T3.4's outcome cell** still described one file written by both streams ⇒ rewritten to the two-file, env-scoped form | `phase3` T3.4 · §3 |
| **B9** no release-time set-and-validate for the floor constant | **verified present** — T6.15 + AC15 + the `phase5` §6 stage input + Pack I fresh-pass/stale-refuse legs — **residual completed by me: task-table order** (T6.14 sat after T6.15) and the §11.1 index row (T6.1…T6.14/AC1…AC14 ⇒ …T6.15/AC15) | `phase6` T6.15/AC15 · `phase5` §6 · §11.1 |
| **B10** no scripts-dir fence row; escape guard misses the live pinned checkout | **verified present** — §3 now carries the 🔴 Scripts-dir row (throwaway dir under FAKE_HOME; refuse `/home/nea/ensemble-src*` or any live checkout), uses the house primitives (`_canon_dir`/`_install_dir_candidates`/`_is_canonical_install_dir`), and names the forbidden live ports | `phase5` §3 |
| **B11** recovery command fails; self-contradicting sentence | **verified present** — the header now gives `git show 013895287:…` + `git cat-file -p f81bcf6ba` and states plainly that `f81bcf6ba` is a **blob**, not a commit | §preamble L10–12 |

**All 24 notes:** folded and verified — N-1 (convention narrowed to the five anchors the set actually carries), N-2 (SC13 → T1.18/AC19), N-3 (§4 map gained the P6 column — **and the six rows it had broken were re-piped by me**: each P5/P6 cell pair had collapsed into one), N-4 (R-A names D6-*/T6.*), N-5 (`:5526` definition site), N-6 (five anchors — class (f)), N-7 (case 8 position-pinned), N-8 ("in input"), N-9 (`journal_in_flight` = latent), N-10 (**D1-18** bounded diagnostic), N-11 (**§2.2b** routed), N-12 (T0.8 file-qualified), N-13 (T1.18 inside §4), N-14 (census row 26 = three mechanisms), N-15 (**M25 = stamp acquisition; M32 added** for the token refusal — **label completed by me** in three places), N-16 (**one canonical count**, now enforced by class (i)), N-17 (`lib.sh:811` qualified as tree-specific), N-18 (spec/implementation split), N-19 (**`gate.sh` + pasted output + evidence artifact**), N-20 (house primitives), N-21 (9797/7979 named), N-22 (G4 ≡ J6, canonical id G4), N-23 (time-dependent dirty count — the read-only architecture file is left untouched, D-F8), N-24 (T0.1 covers the four daemon-side anchors).
