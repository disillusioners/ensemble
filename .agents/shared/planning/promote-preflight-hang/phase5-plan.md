# Phase 5 — Test & evidence (the gate chain's load-bearing phase)

**Deliverables covered:** all of D1–D4 (verification) · **Depends on:** P1–P4 · **Blocks:** the tester functional gate and the giter stage.

---

## 1. Objective

Turn every claim in this plan set into a **mutation-verified pin**, produce the **BASE→HEAD** sandbox proof
that the original symptom is closed, and hand the giter lane a **complete, reproducible** verification surface.

The house rules this phase obeys:

* *"Net-new-failure = full FAILED-list diff vs TRUE pre-fix base, same commands both sides"* — every pack is run
  on **both** legs and the failed-set diff is empty.
* *"regression pins MUST be mutation-verified"* — every pin is **planted** (the defect is re-introduced) and
  shown to turn **RED**, then restored to **GREEN**. The planted digests are recorded.
* *"always pass explicit `tests/` lists"* and the pack lists are **machine-readable** (one repo-relative path per
  line, `#` comments allowed) so the enumeration cannot drift from the invocation.

---

## 2. The pack set

All packs live under `tests/packs/` (shell, house style) with shared helpers under `tests/helpers/`.

| Pack | File | Asserts | Mutation legs |
|------|------|---------|---------------|
| **A — semantics parity** | `tests/packs/preflight_journal_parity_test.sh` | The new read path returns the **identical verdict** and **identical stdout bytes** as the BASE bash scanner on all **22 corpus cases + G1–G5 = 27 fixtures** (Phase 1 §3.3) **[fold: W4]**; `_json_sub` and `journal_update` parity likewise; the three-mode matrix (helper present / absent / refusing) for all 25 distinct census call sites. | M1: revert `journal_read` to the bash scan ⇒ RED. M2: change the scanner's `esc` handling ⇒ RED. M3: drop the `in_str` end-of-file check ⇒ RED. M4: make the helper accept a torn journal ⇒ RED. M5: make `journal_update` write a different byte ⇒ RED. |
| **B — scale** | `tests/packs/preflight_journal_scale_test.sh` | `journal_read` on the **455,783-byte** fixture completes in **< 5 s**; the scaling ratio `T(4N)/T(N) < 6×`; the BASE leg is bounded at 60 s and asserted to **not complete** (the symptom). | M6: restore the O(N) scanner ⇒ RED (and the BASE-leg assertion flips to RED as well). M7: make the helper read the file twice ⇒ RED on the ratio leg. | **[fold: A17: the BASE oracle is the true BASE for every small case; the Option-D chunked proxy is admissible on the 455 KB fixture ONLY after a proxy-validation leg (proxy ≡ true BASE on the full 22-case + G1–G5 corpus (27 fixtures) + one 32 KB fixture); timing claims cite true-BASE/HEAD numbers only.]** |
| **C — symptom repro (BASE→HEAD)** | `tests/packs/preflight_repro_test.sh` | The **same** sandbox + **the same** command + **the same** fixture: **BASE** ⇒ the preflight does not complete (bounded) and writes **no** journal rows; **HEAD** ⇒ the preflight completes and the journal rows appear. | M8: plant the BASE scanner in `promote.sh:225`'s path ⇒ RED. |
| **D — watchdog** | `tests/packs/preflight_watchdog_test.sh` | A planted slow step trips the budget within `budget + 10 s` with exactly one `deadline-breach-*` row, one stderr line, a released lock and exit 78; the **negative control** (a 300 s soak-length phase, and N ≥ 3 healthy promotes) trips **nothing**. | M9: raise the budget past the plant ⇒ RED. M10: remove the lock release from the breach handler ⇒ RED. M11: make the soak-phase exclusion cover the preflight ⇒ RED on the negative control. | **[fold: A1, A2, A3, A4, A8, A9, A11: the double-TERM leg (exactly one `halt` row); the live-shaped-path leg (the row EXISTS — the breach order is load-bearing); the unlocked-path leg (warn + per-run log, NO journal row, `status.sh` exits 1); the trap-install-order leg (a TERM in the former window yields a row + a released lock); the REAL 300 s conservative-mode soak negative control (0 trip rows, 0 kills); the two breach classes (78+`deadline-breach-` prefix vs 143+no prefix) + the `RuntimeMaxUSec` runbook step; the wrapper-contract leg (no grandchild survives a side-effecting step's kill).]** |
| **E — logging** | `tests/packs/preflight_logging_test.sh` | Per-run file is the primary sink (one copy per line); markers ≥ 6 across a preflight with timestamps; **parent SIGKILL** leaves the narrative intact; a write failure is reported; the aggregate pointer + reader fallback work; the benign EPIPE class is gone; **no secret-shaped strings**. | M12: point the shell back at stdout-only ⇒ RED. M13: remove the write-failure fallback ⇒ RED. M14: point the unit's `StandardOutput` back at the aggregate ⇒ RED. | **[fold: A20, A21, A22, A23, A24, A25: the retention fences (5 negative legs); the 4 KiB line cap + the elision marker + the ≥8 KiB corruption pin; the TWO-FILE sink (narrative + payload) with the pointer resolved BY `run_id`; the journald visibility of the write-failure fallback (`logwrite_failed` marker); the in-op markers every 500 files with NO background writer; the sweep's per-run tail via run_id; no cross-env mixing; no secrets in the pointer rows.]** |
| **F — stage lock** | `tests/packs/preflight_stage_lock_test.sh` | A SIGKILLed lock-holder orphans the dir; the next acquisition reclaims it **with** a `lock-stale-break` row carrying all five fields; a successful stage leaves no dir; no double release. | M15: remove the reclaim journal row ⇒ RED. M16: remove the explicit success-path release ⇒ RED. | **[fold: A4: the trap-install-order leg — a TERM between the handler install and `lock_acquire` yields a `halt` row and no orphaned lock.]** |
| **G — census + anchors** | `tests/packs/preflight_census_test.sh`, `tests/packs/preflight_anchor_index_test.sh` | The census equals the Phase 0 table; **0** per-character scans over the journal string remain; every cited anchor still holds. | M17: plant a per-character loop in a lib.sh function ⇒ RED. M18: shift a cited anchor ⇒ RED. |
| **H — daemon-side** | `tests/unit/tools/test_upgrade_preflight_daemon_side.py` (pytest) | The unit argv carries `--property=RuntimeMaxSec=<derived per D2-9>` (**[fold: B2]** — the stale literal `1200` is gone; AC15/AC17 are the single source of truth); the `Standard*Output=append:` targets the per-run **payload** file; the client uses no `PIPE`; the `executor_still_running` row carries the path + phase + elapsed while the 660 s threshold is unchanged; the `unit_properties` `Restart=no` guard still refuses an override. | M19: drop the `RuntimeMaxSec` ⇒ RED. M20: revert the client to `PIPE` ⇒ RED. M21: change the 660 s threshold ⇒ RED. | **[fold: A5, A6: the `unit_properties` plumbing is present; `RuntimeMaxSec` is KIND-SCOPED (promote only — the restart unit carries none) and DERIVED (`max(1200, 900+SOAK_S+120)` = 1320); a live-mode arm with `SOAK_S>300` refuses.]** |
| **J — size-guard semantics** (NEW) **[fold: W1, W2]** | `tests/packs/preflight_size_guard_test.sh` | The **re-scoped** guard (**phase1 D1-17** = `decisions.md` D1-15): the trip condition is the **conjunction** (legacy-class bash walk **AND** size ≥ `JOURNAL_SIZE_GUARD_BYTES` = 98304). Legs: **J1** promote path, over-threshold + legacy class ⇒ **REFUSE loudly** (size-named row + non-zero rc); **J2** `rollback.sh` ⇒ **WARN + continue**; **J3** `restart.sh` ⇒ **WARN + continue**; **J4** `status.sh` ⇒ **WARN + continue**; **J5** the boundary trio (G1 below ⇒ allowed · G2 at ⇒ refuse · G3 above ⇒ refuse, each legacy class); **J6** the **O(N)-path leg** ≡ **phase1's G4** (one leg, two ids: over-threshold and the 455,783 B fixture on the O(N) path ⇒ **NO refusal** — the next-ceremony protection; the canonical id is **G4**); **J7** the over-threshold × interpreter-absent matrix cell (promote / rollback / status ⇒ 3 asserted behaviours). | **M29**: make the guard refuse on `rollback.sh` ⇒ RED. **M30**: consult the threshold on the **O(N)** path ⇒ RED (it would block the next ceremony). **M31**: drop the legacy-class conjunct ⇒ RED (a healthy O(N) read would be refused). |
| **I — ceremony readiness** (NEW) **[fold: A29, A34 SC12; B1, B3, W5, Adv1]** | `tests/packs/preflight_ceremony_readiness_test.sh` | A staged sandbox arm against a **provably-stale pinned scripts dir** **REFUSES BEFORE the nonce is consumed** (no nonce burn; a refusal row naming the sync command); with a synced dir the arm proceeds cleanly; the daemon-side arm row carries the scripts provenance (`scripts_dir`, `lib.sh` sha256, git rev + dirty) and the `PIPELINE_SCRIPTS_REV` stamp; the watchdog token is present in the executor env. **[B1]** The **Q1 hybrid floor**: a **git** pinned dir that **contains** the floor commit ⇒ arms (even if far newer); a git dir that **does not contain** it ⇒ **REFUSE before the nonce burn**; a **missing/unknown floor object or command error ⇒ REFUSE**; a **non-git** pinned dir **with** a stamp ⇒ arms; a **non-git** pinned dir **without** one ⇒ **WARNING-only, never a refusal** (Q2). **[B3]** The guard's input is read from **`<scripts_dir>/lib.sh` at arm time** (bounded first-N-KB), **not** from the executor. **[W5]** The staged-verification **split** is asserted: the daemon-side surface from the **staged artifact**, the shell-side surface from the **pinned checkout at the synced rev** (payloads ship no `scripts/` since v0.27.4); `git rev` **and** `lib.sh` sha256 are **required** arm-row provenance fields. | M22: plant a stale stamp ⇒ RED. M23: move the refusal after `consume_pending_action` ⇒ RED (the nonce would be burned). M24: drop the provenance row ⇒ RED. **M25: take the stamp from the executor instead of the file ⇒ RED** (the guard must gate before the nonce burn — **this is the STAMP-ACQUISITION leg, not the token leg**). **M32: make `promote.sh` ignore the `ENSEMBLE_PROMOTE_WATCHDOG=1` token ⇒ RED** (the mixed-generation token refusal — `phase6-plan.md` T6.6 / AC6 — previously had **no** mutation leg). **M26: plant a non-ancestor pinned dir ⇒ RED.** **M27: plant a missing floor object ⇒ RED (must refuse).** **M28: make the non-git branch refuse ⇒ RED (must warn only).** |

**Pack H** is pytest (house precedent: `tests/unit/tools/test_upgrade_executor_systemd_service.py`,
`test_promote_cgroup_survivorship_python.py`); packs A–G are shell (house precedent:
`tests/packs/promote_soak_never_green_test.sh`).

---

## 3. The sandbox fence (mandatory in every pack) — **CANONICAL**

> **This section is the ONE canonical statement of the sandbox-fence contract for the whole lane.** Every other file (notably `phase0-plan.md` T0.7/AC7) **cross-references** this section and does not restate the contract.

`tests/helpers/preflight_pack_common.sh` owns it (model: `tests/helpers/soak_gate_pack_common.sh:1`-`:38`).

| Fence | Rule | Assertion |
|-------|------|-----------|
| **FAKE_HOME** | `mktemp -d` fake home; the install root is resolved **inside** it. | A self-test leg resolves a live-shaped path ⇒ the pack **refuses** (rc ≠ 0). |
| **Escape guard** | The install root AND the **scripts dir** are checked **before** every write, using the **house primitives** rather than a literal-path prefix: `_canon_dir` (`lib.sh:427`) → `_install_dir_candidates` (`lib.sh:444`) → `_is_canonical_install_dir` (`lib.sh:480`) — they were added after the **2026-10-12 double live-install deletion** and cover the Layer-1 HOME shapes a naive prefix cannot. The literal guard remains as a belt: `case "$INSTALL_DIR" in /home/nea/agents-ensemble*) refuse ;; esac` **and** `case "$SCRIPTS_DIR" in /home/nea/ensemble-src*) refuse ;; esac`. | Both guards are exercised by the self-test leg. **[fold: approver-002 blocker 10, N-20]** |
| **Env scrub** | Per-**variable** scrub: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT …`. **`env -u POSTGRES_*` is BANNED** (bash does not glob env names ⇒ it is a silent NO-OP). | A grep-assertion that the banned idiom appears **nowhere** in the lane's test code. |
| **Port** | An explicit, non-ambient port (never `DAEMON_PORT`). **The forbidden live ports are named: 9797 (live) and 7979 (demo)** — the symmetric counterpart of the Scratch-DB row's forbidden names. | Asserted; a pack that binds 9797/7979 **refuses**. **[fold: N-21]** |
| **Scripts dir** 🔴 | The **pinned scripts dir** is the pack's first-class subject (Pack I plants a stale stamp / a non-ancestor dir / a missing floor object, and the daemon reads `<scripts_dir>/lib.sh` at arm time). The fence: the pack's scripts dir is a **throwaway dir under the FAKE_HOME root**; a resolved scripts dir under **`/home/nea/ensemble-src`** (the live pinned checkout) or any live checkout ⇒ **REFUSE**; the escape guard runs **before** every read/write of that dir. **Without this row the fence would leave the one new path surface unfenced — the 2026-10-12 live-install-deletion class.** | The self-test leg plants a live-shaped scripts dir and the pack **refuses** (the refusal is itself asserted). **[fold: approver-002 blocker 10]** |
| **Scratch DB** | A throwaway LOGIN role + `createdb -O` **over TCP** (never a `/tmp` socket path — psycopg mis-parses `path:port` as a dbname); `DROP` at cleanup. | Asserted; the live DB names are refused. |
| **No daemon boot** | The packs exercise the **shell pipeline** and the **daemon-side pure functions** — no daemon is booted, no LLM is called, no PG is contacted by the shell packs. | Asserted by construction; the pack lists no daemon start. |
| **No live promote** | `promote.sh` is exercised only against a scratch install root with `ENSEMBLE_UPGRADE_LIVE` unset (⇒ `require_live_guard` refuses live, `lib.sh:880`-`:893`). | Asserted: an attempt to target `live` is refused by the guard. |

---

## 4. The BASE→HEAD discipline

1. The same command list runs on **both** legs: the **true pre-fix base** (the merge-base of the lane's first
   commit vs `origin/latest`) and **HEAD**.
2. The **FAILED-list diff** must be empty (net-new = ∅), and every previously-red assertion must be **green at HEAD**.
3. Order/load-sensitive reds are re-run **3× in isolation on each side** before being classified (house rule).
4. The **BASE** leg of the symptom pack is **bounded** (60 s) and asserts *"did not complete"* — it is never run
   to completion (≈ 45–61 min).

---

## 5. The house gate chain (what this plan set feeds, in order)

| Gate | Owner | Input from this plan set | Exit criterion |
|------|-------|---------------------------|----------------|
| 1. Architect enrichment | architect | this plan set + `evidence/` | a ruling on **OQ1** (D2 shape), **OQ2** (D1 refusal policy), **OQ3** (D4b) |
| 2. Reviewer council | reviewer[v2] | the enriched plan | findings folded |
| 3. Fresh approver | approver | the folded plan | approval |
| 4. Implement | developer | Phases 1–4 | the code + the pack set |
| 5. Code council | reviewer[v2] | the diff | 0 critical |
| 6. Tidier | tidier | the diff | hygiene findings folded |
| 7. **Tester functional gate** | tester | **Phase 5** | packs **A–J** green on both legs; net-new = ∅; mutations proven |
| 8. Giter stage | giter | the reviewed tree | a staged release with the §6 adjudication inputs |
| 9. Staged verification | tester | the staged artifact | the artifact's integrity + the lane's content verified **from the staged binary** |

**Stage-time prerequisites (the giter lane's inputs — see §6).**

---

## 6. Stage adjudication inputs (what the giter lane needs from this lane)

| Input | Value / source |
|-------|----------------|
| **`PIPELINE_SCRIPTS_FLOOR_COMMIT`** | the shipped value **must** be set by the release process (T6.15) and **validate** against the built tree (`git merge-base --is-ancestor <shipped-floor> HEAD` ⇒ pass); an unset / unresolvable / non-ancestor value **fails the release**; the daemon constant and the release manifest must carry the same value |
| **Migration delta** | **EMPTY** — this lane adds/changes **no** `.sql` file (shell + python pipeline changes only). The migration set is byte-identical to the live release. |
| **Rollback posture** | `rollback_safe = true`, **source = explicit**. Standing-rule reasoning: (a) no schema mutation ships; (b) the journal **format** is unchanged (the write path is untouched); (c) the only runtime changes are in the executor-launch path (log path + unit property) and are **additive**; (d) **disclosed caveat** — rolling back restores the O(N²) read, i.e. a **capability regression on rollback, not an incompatibility**; the operator must not treat a rolled-back release as "fixed". |
| **Version slot** | **Derived by live census at stage time** (no reservation). The sibling event-loop sync-DB lane (`wi-3f5bea95`) may also stage; the live census order decides. The lane owner re-derives; this plan states **no** version claim. |
| **Payload impact** | `scripts/upgrade/*` (shell + python) + `daemon/tools/upgrade_journal.py` + `daemon/tools/upgrade_tools.py` + `daemon/services/upgrade_journal_sweep.py`. **No** agent prompt, **no** frontend, **no** plugin, **no** migration. |
| **Live-contact attestation** | **ZERO live contact**: no live promote, no `current` symlink flip, no live `state.json` write, no daemon boot, no daemon restart. The sandbox's install root is a `mktemp` fake home; the live install is only ever **read** (the incident fixture, read-only) and is asserted untouched by a fence self-test. |
| **Evidence** | `evidence/FORENSICS.md`, `evidence/CODE-PATHS.md` (unmodified) + the tester RESULTS file for the functional gate. |

---

## 7. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| **T5.1** | Write `tests/helpers/preflight_pack_common.sh` (the fence) + its self-test legs. | Every pack that sources it refuses a live-shaped install root; the per-variable scrub is used; the banned `env -u POSTGRES_*` idiom appears nowhere (grep-asserted). | `tests/helpers/` (new) | P0 | R6 | AC1 |
| **T5.2** | Implement **Pack A** (parity) + the corpus fixtures. **[fold: W4]** | **25/27 fixtures** (22 corpus + 3 guard rows), 0 differences; the three-mode matrix passes for all 25 distinct call sites' scripts. | `tests/packs/preflight_journal_parity_test.sh`, `tests/fixtures/journal/` | P1 | R2 | AC2 |
| **T5.3** | Implement **Pack B** (scale) with the bounded BASE leg. | HEAD < 5 s on the 455 KB fixture; ratio < 6×; the BASE leg asserts non-completion. | `tests/packs/preflight_journal_scale_test.sh` | P1 | — | AC3 |
| **T5.4** | Implement **Pack C** (BASE→HEAD symptom repro). | BASE: not completed + zero journal rows. HEAD: completed + the expected rows. | `tests/packs/preflight_repro_test.sh` | P1, P2 | R6 | AC4 |
| **T5.5** | Implement **Pack D** (watchdog) incl. the negative control. | A planted slow step trips (row + stderr + lock released + exit 78); the 300 s negative control trips **nothing** across N ≥ 3 healthy runs. | `tests/packs/preflight_watchdog_test.sh` | P2 | R1 | AC5 |
| **T5.6** | Implement **Pack E** (logging). | Per-run primary sink; ≥ 6 markers; parent-SIGKILL survival; write-failure reporting; pointer + reader fallback; no EPIPE class; no secrets. | `tests/packs/preflight_logging_test.sh` | P3 | — | AC6 |
| **T5.7** | Implement **Pack F** (stage lock). | The orphan reproduces; the reclaim journals all five fields; a successful stage leaves no dir. | `tests/packs/preflight_stage_lock_test.sh` | P4 | — | AC7 |
| **T5.8** | Implement **Pack G** (census + anchors) and the machine-readable pack lists. | The census equals the Phase 0 table; 0 per-character journal scans remain; the anchors hold. | `tests/packs/preflight_census_test.sh`, `tests/packs/preflight_anchor_index_test.sh`, `tests/packs/preflight_census.txt` | P0 | — | AC8 |
| **T5.9** | Implement **Pack H** (daemon-side pytest). **[fold: B2]** | The argv carries `RuntimeMaxSec=<derived per D2-9>` (> 900); the log targets are per-run; no client `PIPE`; the enriched row; the `Restart=no` guard intact. | `tests/unit/tools/test_upgrade_preflight_daemon_side.py` | P2, P3 | — | AC9 |
| **T5.10** | Run the **mutation matrix** (M1–M32) and record the planted digests + RED/GREEN results. **[fold: W4, Adv1; W2 — M29–M31 are Pack J's legs; approver-002 N-15 — M32 is the token-refusal leg]** | Every mutation turns the pinned assertion **RED** and the restore returns it **GREEN**; the matrix is recorded in the tester RESULTS. | — | T5.2–T5.9 | — | AC10 |
| **T5.11** | Run the **dual-leg net-new diff** (same commands on base and HEAD) + the 3× isolation re-runs for order-sensitive reds. | The FAILED-list diff is empty; every previously-red assertion is green at HEAD. | — | T5.10 | — | AC11 |
| **T5.12** | Assemble the **stage adjudication pack** (§6) and the **ceremony notes** (the rollback caveat, the version-slot derivation, the live-contact attestation). | The giter lane can stage without re-deriving anything. | — | T5.11 | R9 | AC12 |
| **T5.13** | Extend **Pack D** with the amendment legs: double-TERM, live-shaped row, unlocked-path split, trap-install order, REAL 300 s soak control, the two breach classes, the wrapper contract. **[fold: A1, A2, A3, A4, A8, A9, A11]** | Each leg has a planted-mutation RED ⇒ GREEN pair; the pack exits non-zero if any leg is missing. | `tests/packs/preflight_watchdog_test.sh` | P2 | R14, R15, R20, R22 | AC13 |
| **T5.14** | Extend **Pack E** with the amendment legs: retention fences, atomicity/elision/≥8 KiB corruption, the two-file sink + pointer-by-run_id, the journald fallback, the in-op markers, the sweep per-run tail, cross-env isolation, pointer-row secrets. **[fold: A20, A21, A22, A23, A24, A25]** | Each leg has a planted-mutation RED ⇒ GREEN pair; the pack exits non-zero if any leg is missing. | `tests/packs/preflight_logging_test.sh` | P3 | R-B … R-I | AC14 |
| **T5.15** | Extend **Pack H** with the kind-scoping + derivation legs and the live-mode `SOAK_S>300` refusal. **[fold: A5, A6]** | The promote argv carries the derived property; the restart argv carries none; the live-mode refusal and the sandbox acceptance are both proven. | `tests/unit/tools/test_upgrade_preflight_daemon_side.py` | P2 | R16, R17 | AC15 |
| **T5.16** | Implement **Pack I** (ceremony readiness) — the stale-pin refusal **before** the nonce burn, the **Q1 hybrid floor** (git ancestry primary; non-git warning-only), the **B3 arm-time acquisition** (the daemon reads the stamp from `<scripts_dir>/lib.sh`, bounded first-N-KB), the arm-row provenance (**required** `git rev` + `lib.sh` sha256), the watchdog token, and the **W5 staged-verification split**. **[fold: A29, A30, A34 SC12; B1, B3, W5, Adv1]** | A stale pinned dir ⇒ refusal, **no nonce burn**, a refusal row naming the sync command; a git dir containing the floor commit ⇒ a clean arm (even if far newer); a non-git dir without a stamp ⇒ **warning only**; the guard's input is read from the **file**, not the executor; the arm row carries the required provenance; the executor env carries the token. | `tests/packs/preflight_ceremony_readiness_test.sh` (new) | P6 (the **artifact**; the pack's *content* depends on phase6's spec, not on phase6's implementation — see N-18) | R-A, R-G, R18 | AC16 |

---

## 8. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The fence helper refuses a live-shaped install root (self-test leg), uses a per-variable `POSTGRES_*` scrub, and the banned `env -u POSTGRES_*` idiom appears **nowhere** in the lane's test code. |
| **AC2** | Pack A: **27/27** fixtures (22 corpus + 3 guard rows) with 0 verdict differences and 0 stdout-byte differences **[fold: W4]**; the three-mode matrix passes for all 25 distinct call sites. |
| **AC3** | Pack B: HEAD < 5 s on the 455,783-byte fixture; `T(4N)/T(N) < 6×`; the BASE leg is bounded at 60 s and asserts non-completion. |
| **AC4** | Pack C: the same sandbox/command/fixture shows BASE non-completion with zero journal rows and HEAD completion with the expected rows. |
| **AC5** | Pack D: the planted slow step trips exactly one `deadline-breach-*` row + one stderr line + a released lock + exit 78; the 300 s negative control trips **nothing** across N ≥ 3 healthy runs. |
| **AC6** | Pack E: the per-run file is the primary sink (one copy per line), ≥ 6 timestamped markers, the narrative survives a parent SIGKILL, write failures are reported, the reader falls back correctly, the EPIPE class is gone, no secret-shaped strings. |
| **AC7** | Pack F: the orphan reproduces under SIGKILL; the reclaim journals all five fields; a successful stage leaves no lock dir and releases exactly once. |
| **AC8** | Pack G: the census matches (25 distinct call sites + the 5 extra O(N²) sites); **0** per-character journal scans remain at HEAD; the anchors hold; a planted scan or a shifted anchor turns the pack RED. |
| **AC9** | Pack H: the argv carries `RuntimeMaxSec=<derived per D2-9>` (> the 900 s deadline) **[fold: B2]**, the log targets are per-run, the client uses no `PIPE`, the row is enriched, and the 660 s threshold + `Restart=no` guard are unchanged. |
| **AC10** | Every one of M1–M32 turns the pinned assertion **RED** and the restore returns it **GREEN**; the planted digests are recorded. |
| **AC11** | The dual-leg FAILED-list diff is **empty**; every previously-red assertion is green at HEAD. |
| **AC12** | The stage adjudication pack is complete: migration delta EMPTY, the explicit rollback posture + the rollback caveat, the live-census version-slot derivation, and the zero-live-contact attestation. |
| **AC13** | Pack D carries all amendment legs (double-TERM, live-shaped row, unlocked split, trap order, REAL 300 s soak control, two breach classes, wrapper contract), each with a planted RED ⇒ GREEN pair. |
| **AC14** | Pack E carries all amendment legs (retention fences, 4 KiB cap + elision + ≥8 KiB corruption pin, two-file sink, pointer-by-run_id, journald fallback, in-op markers, sweep per-run tail, cross-env isolation, pointer-row secrets), each with a planted RED ⇒ GREEN pair. |
| **AC15** | Pack H proves the `unit_properties` plumbing, the kind-scoping (restart unit carries no RuntimeMaxSec) and the derivation (1320 / 1800), plus the live-mode `SOAK_S>300` refusal and the sandbox acceptance. |
| **AC16** | Pack I proves the ceremony-readiness contract: a stale pinned scripts dir **refuses before the nonce is consumed** with a refusal row naming the sync command; a synced dir arms cleanly; the arm row carries the scripts provenance + `PIPELINE_SCRIPTS_REV`; the executor env carries `ENSEMBLE_PROMOTE_WATCHDOG=1`. |
| **AC17** | The **count-sweep gate** (W4/A2/A3 + approver-001 blocker 4) runs as an executable check over the plan set and exits 0 across **eight** classes: corpus **22 + G1–G5 = 27**; mutation matrix **M1–M32** (the span rule — any lower/higher live span trips); pack set **A–J**; no live `RuntimeMaxSec=1200` pin literal; no phase-3 single-file-era wording; **(f)** the anchor class (`lib.sh:5569` gone); **(g)** the superseded-identifier class (e.g. `MIN_PIPELINE_SCRIPTS_REV` gone); **(h)** the **resolution + TOPIC class** — every cited D-id/T-id resolves to an existing row **in the file it is cited for**, every id is defined somewhere, the §5.1 "Encoded in" cells name a file that defines the id they cite, **and every §11.1 map row resolves to its subject (its topic anchor must match the defining row's topic)**. The gate (`gate.sh`, executable, read-only) allows exactly two categories — quoted meta-mentions and supersession records (explicitly exempted at the cell); **the §11.1 map rows are NOT exempt**, so it cannot be satisfied by deleting the audit trail; its output + exit code are quoted in §9 and the lane report. |


---

## 9. Fold log (architect → Phase 5), 2026-10-29

| A# | Subject | Status | Where |
|----|---------|--------|-------|
| A1 | double-TERM pin | folded | Pack D, T5.13, AC13 |
| A2 | live-shaped-path row assertion | folded | Pack D, T5.13, AC13 |
| A3 | unlocked-path breach leg (`status.sh` exits 1) | folded | Pack D, T5.13, AC13 |
| A4 | trap-install-order leg | folded | Pack D + Pack F, T5.13, AC13 |
| A5 | `unit_properties` plumbing + kind-scoping | folded | Pack H, T5.15, AC15 |
| A6 | derived RuntimeMaxSec + live-mode `SOAK_S>300` refusal | folded | Pack H, T5.15, AC15 |
| A8 | REAL 300 s conservative-mode soak negative control | folded | Pack D, T5.13, AC13 |
| A9 | two breach classes + `RuntimeMaxUSec` runbook step | folded | Pack D, T5.13, AC13 |
| A11 | wrapper contract leg | folded | Pack D, T5.13, AC13 |
| A17 | differential harness (proxy-validation leg) | folded | Pack B (the harness itself is Phase 0 T0.10) |
| A20 | retention fences | folded | Pack E, T5.14, AC14 |
| A21 | atomicity contract + ≥8 KiB corruption pin | folded | Pack E, T5.14, AC14 |
| A22 | two-file sink + pointer-by-run_id + journald fallback | folded | Pack E, T5.14, AC14 |
| A23 | in-op markers / no background writer | folded | Pack E, T5.14, AC14 |
| A24 | sweep per-run tail + env-scoped mkdir + `_log_tsl` reuse | folded | Pack E, T5.14, AC14 |
| A25 | cross-env isolation + pointer-row secrets | folded | Pack E, T5.14, AC14 |
| A29 | the stale-pin refusal before the nonce burn | folded | **Pack I (NEW)**, T5.16, AC16, `plan-overview.md` SC12 |
| A30 | the watchdog token | folded | Pack I, T5.16, AC16 |
| A34 | SC12 wiring | folded | Pack I, T5.16, AC16 |

**Count-sweep gate — EXECUTABLE ARTIFACT.** The gate is `gate.sh` (this directory, mode `755`), run as
`bash .agents/shared/planning/promote-preflight-hang/gate.sh` (`--quiet` = one line per class). It reads the nine plan
documents **only** — it never executes a pipeline script (no promote/stage/rollback/adopt-unit), never touches the live
install, never resolves a live-shaped path (it opens files by relative name inside its own directory), and is
read-only/idempotent: the same input yields the same output and exit code. **It exits non-zero on any failure.**

**Canonical size: 12 executable checks = the EIGHT classes (a)–(h) + the (g+) positive assertion + the (i)
artifact/count check.** Class (h) carries three sub-assertions: **(h1)** file-qualified resolution, **(h2)**
every-referenced-id-defined-somewhere, **(h3)** **map-row TOPIC resolution** (the §11.1 rows are **not** exempt). This
number is itself asserted by check (i) against this text, so the recorded result cannot drift from the artifact.

**RUN — 2026-10-29, final fold (round 6), verbatim:**

```
[PASS] (a) corpus cardinality — one canonical '22 + G1–G5 = 27'; no stale counts
[PASS] (b) mutation-matrix span — every LIVE span claim is M1–M32
[PASS] (c) pack set — no LIVE 'packs A–H' / 'A–I' (canonical A–J)
[PASS] (d) RuntimeMaxSec — no LIVE '=1200' pin literal
[PASS] (e) phase3 — no single-file-era wording / no LIVE D3-3 pointer reference
[PASS] (f) anchors — the stale anchors are gone (lib.sh:5567 / :1041 / :1053 / :1169 / :1173; stage.sh:577)
[PASS] (g) superseded identifiers — no LIVE 'MIN_PIPELINE_SCRIPTS_REV'
[PASS] (g+) the replacement identifier 'PIPELINE_SCRIPTS_FLOOR_COMMIT' IS present
[PASS] (h1) RESOLUTION — every file-qualified citation resolves in the file it is cited for
[PASS] (h2) RESOLUTION — every referenced id is defined somewhere in the set
[PASS] (h3) RESOLUTION/TOPIC — every §11.1 map row's anchor matches the DEFINING row's topic (map rows NOT exempt)
[PASS] (i) ARTIFACT — the recorded result names an executable gate.sh, and the set's stated check count matches the gate's
------------------------------------------------------------------------------
RESULT: PASS — 12/12 executable checks passed
EXIT CODE: 0
```

**The gate is not vacuous — it was mutation-tested (2026-10-29, round 6), one planted defect per class, each on a
throwaway copy of the set:** (a) plant `22 + 3` / `**25** fixtures` ⇒ RED · (b) plant a live `M1–M28` span ⇒ RED ·
(c) plant a live `packs A–I` ⇒ RED · (d) plant `RuntimeMaxSec=1200` ⇒ RED · (e) plant `lives in **one per-run file**`
⇒ RED · (f) plant `lib.sh:5569` ⇒ RED · (g) plant `MIN_PIPELINE_SCRIPTS_REV` ⇒ RED · (h1) point a real
`` `phase1-plan.md` D1-13 `` citation at `phase3-plan.md` ⇒ RED · (h2) mention an undefined `D7-1` ⇒ RED · (h3)
re-anchor a map row to another row's topic ⇒ RED. Restored ⇒ GREEN. **Class (h3) evaluates 61 map rows** (counted,
not assumed) — it cannot pass by finding nothing.

**The first-run record (round 5, pre-fix, retained as provenance).** Class (h) on its **first** run reported **13
unresolved file-qualified citations + 18 ids defined nowhere** — the approver's nine stale §5.1 pointers, the dangling
`D2-3` (R20), the `phase2-plan.md` **`D2-3`** citations in R1/R8, the `T3.11`/`T3.12`/`T3.13`/`T3.15` phase3 pointers
(the hyphenated-vs-dotted task-id drift — N2), the shorthand file refs (`phase0`, `phase5`, …) that made the
§11.1/§13/§14 cells mis-attribute their ids, and four cells whose ids sat after a *different* file ref. All were
corrected. Class (h) is the reason the approver's blockers 1–3 cannot recur silently — **but only after round 6
narrowed it**: the earlier form asserted *existence* only and explicitly **exempted the §11.1 map rows**, which is
exactly why blocker 1's mis-pointed rows passed it (approver-002 blocker 2). Round 6 added **subject** resolution
((h3)) and removed the map-row exemption.

**Superseded:** none in Phase 5. **Already present:** the pack set **A–J**, the mutation matrix **M1–M32**, the fence
helper contract (§3) and the dual-leg net-new discipline (§4) — endorsed; **M22–M28** are **added** for Pack I
(M22 stale stamp, M23 refusal-after-nonce, M24 provenance row, **M25 stamp-from-the-file (the acquisition leg — *not* the token leg, per N-15)**, M26 non-ancestor dir, M27 missing floor object, M28 non-git branch must warn only), **M29–M31** for **Pack J**
(M29 the guard refuses on `rollback.sh` ⇒ RED, M30 the threshold is consulted on the **O(N)** path ⇒ RED, M31 the legacy-class conjunct is dropped ⇒ RED), and **M32** for the **mixed-generation token refusal** (M32: `promote.sh` ignores the token ⇒ RED).

| **T5.17** | **The count-sweep gate (W4/A2/A3 + approver-001 blocker 4)** — a **pre-approver** step, **EIGHT** executable classes over the plan set: **(a)** the corpus is stated as **22 + G1–G5 = 27** everywhere (no live `15` / `≥ 14` / `15/15` remnants); **(b)** the mutation matrix is stated as **M1–M32** everywhere — the class asserts the **span rule** (any live span below or above M1–M32 trips, so a stale `M1–M24` / `M1–M28` regression trips); **(c)** the pack set is **A–J** everywhere (no live `A–H` / `A–I` remnants); **(d)** **no live `RuntimeMaxSec=1200` pin literal** survives (the derivation is the single source of truth); **(e)** the phase-3 **single-file-era** wording is gone; **(f)** the **anchor class** — the `lib.sh:5569` mis-anchor is gone (the `stat -f '%m'` site is `lib.sh:5567`) — *this class was missing from the rule although it was in the recorded result*; **(g)** the **superseded-identifier class** — no live superseded identifier survives (e.g. **`MIN_PIPELINE_SCRIPTS_REV`**, the pre-B1 floor name) — *this class is what would have caught W3*; **(h)** the **RESOLUTION + TOPIC class** — three sub-assertions: **(h1)** **every cited D-id/T-id resolves to an existing row in the file it is cited for** (a file-qualified citation `` `phase2-plan.md` D2-10 `` must have `D2-10` **defined** in `phase2-plan.md`; the §5.1 "Encoded in" cells must name a file that defines the id); **(h2)** every id mentioned anywhere is defined **somewhere** in the set; **(h3)** every **§11.1 cross-file map row resolves to its SUBJECT** — the row's cited id must exist **and** its topic anchor must match the **DEFINING row's topic** (existence alone is insufficient: that is how blocker 1's mis-pointed rows survived). **The map rows are NOT exempt from (h)** — a map row must itself resolve; *this class is what would have caught the approver's blockers 1–3 (the nine stale §5.1 pointers, the dangling `D2-3`, the A7/A8 swap and the D2-*/D3-* series collisions)*. **[fold: approver-002 blocker 2 — subject resolution + the map-row exemption narrowed to nothing]** **Run it and record the result in the lane report.** **[fold: W4, A2, A3, approver-001 blocker 4]** | The sweep exits 0 on the folded plan set; each of the **eight** classes is asserted by an executable check (not by eye); the gate's rules allow exactly three categories — **quoted meta-mentions** (a backtick- OR curly-quoted id/phrase), **supersession records** (exempt **at the cell**, stated there) and the **cross-file map rows** (§11.1) — so it cannot be satisfied by deleting the audit trail. **The gate is the executable artifact `gate.sh` in this directory** (`bash .agents/shared/planning/promote-preflight-hang/gate.sh`; `--quiet` for one line per class): it reads the plan documents **only**, never executes a pipeline script, never touches the live install or a live-shaped path, is read-only/idempotent, and **exits non-zero on any failure**; its actual output + exit code are pasted in §9 and in the lane report. **[fold: N-19]** | `gate.sh` (executable) + plan set + the lane report | T5.16 | — | AC17 |