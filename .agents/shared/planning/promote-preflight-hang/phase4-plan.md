# Phase 4 — D4: Secondary defects

**Deliverables covered:** D4(a) the `stage.sh` lock-dir leak · D4(b) the daemon's `executor_still_running`
"benign-detach (no kill)" disposition.
**Depends on:** P0 · **Blocks:** P5.

---

## 1. Objective

Fold in the two small defects the forensic lane surfaced, and **dispose** of each with an explicit,
evidence-backed recommendation rather than an unexamined "fix":

* **D4(a)** — `stage.sh` leaves its lock dir behind (the orphan `rollback.lock.d.stale.2480459`, owner `2182592`,
  heartbeat `2026-10-28T10:04:46Z`) → the orphaned-lock confusion that forced a **manual** house stale-break
  before the 17:05 arm.
* **D4(b)** — the daemon **observed** the 37-minute stall at 17:16:11 and deliberately did nothing
  (*"still running after 660s — benign-detach (no kill …)"*).

---

## 2. D4(a) — the `stage.sh` lock leak

### 2.1 What is VERIFIED at TIP (and what is not)

| Fact | Anchor / evidence | Status |
|------|-------------------|--------|
| `stage.sh` acquires the lock | `scripts/upgrade/stage.sh:364` | **verified** |
| `stage.sh` installs the signal handlers + swaps the EXIT trap | `scripts/upgrade/stage.sh:375` (`_trap_install_signal_handlers "stage:lock"`) | **verified** |
| The EXIT trap body releases the lock | `scripts/upgrade/lib.sh:2034`-`:2039` (`_trap_safe_exit` → `lock_release`) | **verified** |
| `lock_release` is owner-guarded and idempotent (no dir ⇒ rc 0) | `scripts/upgrade/lib.sh:1745`-`:1759` | **verified** |
| `stage.sh` has **no explicit** `lock_release` on its success path | `scripts/upgrade/stage.sh:744` (`exit 0`) — `grep -n lock_release scripts/upgrade/stage.sh` ⇒ **no hits** | **verified** |
| The two 09:5x attempts died **with** a `halt … at phase stage:lock` journal row ⇒ the TERM trap fired ⇒ the lock was released | `evidence/FORENSICS.md` §2-6, §3.4 | **verified** |
| The surviving orphan's owner (pid `2182592`, run `run-20261028-095749-2182592`) has **no** corresponding `halt` row ⇒ **its trap never fired** ⇒ it died in a **trap-bypassing** way (SIGKILL class) | `evidence/FORENSICS.md` §3.4 (the journal shows exactly two `halt` rows: 09:51:36Z and 09:54:59Z) | **verified by inference from the row set** |
| The **exact** death mode of attempt #3 (SIGKILL from an outer harness? OOM? the operator's own kill?) | — | **NOT ESTABLISHED** — Phase 0 must reproduce it |

⇒ **Corrected framing of D4(a):** the defect is **not** "stage.sh leaks the lock on success". A *normal* stage
exit releases the lock via the EXIT trap. The defect is that **the lock's release is trap-only**, so a
**trap-bypassing death (SIGKILL class) orphans the lock dir** — and the **reclaim is not journaled**, so the
next operator has no durable audit row explaining why the lock was breakable, and (as on 2026-10-28) performs a
**manual** stale-break believing it necessary — when the automatic reclaim would in fact have applied
(`lib.sh:1689`-`:1693`: a **dead owner** breaks the lock even with a fresh heartbeat).

### 2.2 The fix (three parts)

| # | Part | Rationale | Anchor |
|---|------|-----------|--------|
| **1** | **Journal the reclaim.** Every stale-break / dead-owner break writes a `lock-stale-break` journal row naming: the reclaimed owner pid, the reclaimed run_id, the heartbeat age, the reason (`stale-heartbeat` / `dead-owner`) and the reclaiming run_id. | The reclaim is currently a **stdout `_log` line only** (`lib.sh:1679`, `:1691`) — invisible in the durable audit trail. A journal row makes the orphan's lifecycle auditable and turns "the operator broke it by hand" into "the pipeline broke it, here is why". | `lib.sh:1670`-`:1694` |
| **2** | **Explicit success-path release** in `stage.sh` (a `lock_release` immediately before `exit 0`), with the `_LOCK_RELEASED` guard honoured so the EXIT trap does not double-release. | Belt-and-braces: the lock's lifetime no longer depends on a trap being installed and surviving. `lock_release` is idempotent, so the belt is free. | `stage.sh:744`; `lib.sh:2028`, `:2034`-`:2039` |
| **3** | **Reproduce the leak** in the sandbox (Phase 0 / Phase 5): SIGKILL a stage-like run holding the lock ⇒ the lock dir survives ⇒ the reclaim (with the new journal row) reclaims it **loudly**. | The fix is only justified if the leak is *demonstrated*. Without the repro, part 1 is an unanchored improvement and part 2 is a guess. | Phase 0 T0.4 / Phase 5 Pack F |

**Explicitly NOT in this lane:** changing the stale-break *policy* (the age threshold `LOCK_STALE_S=300`, the
dead-owner rule, the "a live owner is never broken" invariant at `lib.sh:1683`-`:1684`). Those are sound and are
the house's crash-recovery contract.

### 2.2b The stale-break **residue** — ROUTED (approver-002 N-11)

`lock_acquire`'s stale-break **moves** the lock dir to `${lock}.stale.$$` (`lib.sh:1680`, `:1692`) and **never
removes it** ⇒ a `rollback.lock.d.stale.*` residue accumulates (one exists in the live install today). This is
**outside** D4(a)'s three parts (journal the reclaim · belt the success path · reproduce the leak) and outside this
lane's blast radius — it is *residue hygiene*, not correctness. ⇒ **ROUTED**: recorded here and in the routed
board item (the D4b-4 sibling scope) so the **lock-protocol owner** picks it up. This lane adds **no** deletion
of a live lock dir — deleting a lock dir is exactly the live-install-deletion class the fence forbids.

### 2.3 The routed question

**Who SIGKILLed the stage run?** The FORENSICS meta-observation records that a lane's own `bash` call wedged at
0 % CPU for 29 minutes during the investigation — *"the failure mode under investigation … reproduces inside the
very tooling used to investigate it"*. An outer harness timeout (SIGTERM → grace → **SIGKILL**) is the most
likely SIGKILL source. That is a **pipeline-hygiene** concern, not an upgrade-script concern ⇒ **routed**, and
recorded in the lane's report so the next operator is not left guessing.

---

## 3. D4(b) — the daemon's `executor_still_running` disposition

### 3.1 The observation (verified)

* `daemon/services/upgrade_journal_sweep.py:2281`-`:2291` writes, at 660 s:
  *"run_id=… pid=… still running after 660s — benign-detach (no kill; own process group; correlate with
  data/upgrade.log)"*.
* `DEFAULT_REAPER_TIMEOUT_SECONDS = 660` (`daemon/services/upgrade_journal_sweep.py:102`), derived at `:98`-`:101`
  from *livez 60 + readyz 120 + soak 300 + overhead ≈ 490 s observed minimum* + headroom.
* On 2026-10-28 the daemon fired this row at **17:16:11** — 660 s into a stall that would run to **17:42:44**.

### 3.2 Recommendation: **ROUTE to its own commission** (do not change the kill contract here) — ✅ **CONFIRMED-BY-LEADER 2026-10-29 (architect RULING 3; leader ruling 3: “benign-detach kill-contract stays ROUTED to its own commission”)**

Rationale, in the house's decision style:

| # | Argument |
|---|-----------|
| **1** | The benign-detach is **load-bearing**, not incidental. The executor is *designed* to survive the daemon's death — it runs in its own transient unit with its own cgroup (`daemon/tools/upgrade_journal.py:2664`-`:2677`). A daemon-side kill that fires on a timer would reintroduce exactly the "the daemon's cgroup teardown killed the ceremony" class the v0.18.0 service lane was built to eliminate (`daemon/tools/upgrade_journal.py:2185`-`:2192`). |
| **2** | **660 s is a healthy-path number, not a stall number.** It is derived from the *observed minimum* of a healthy promote (490 s). Firing a kill at 660 s would kill a legitimate promote that is merely slow. |
| **3** | A correct escalation needs a **"stalled" predicate**, not a timer: *no journal progress AND no heartbeat advance for N seconds*. The daemon-side liveness predicate (`_executor_alive_recent`, `daemon/services/upgrade_journal_sweep.py:2008`) keys on the **txn heartbeat**, which only exists **after** `journal_open_txn` — i.e. it cannot see a **pre-txn** stall (which is exactly what happened). Building that pre-txn signal is a daemon-side design task with its own evidence lane. |
| **4** | **Blast radius.** Changing the reaper contract inside a shell-script lane would make the lane's diff span the daemon's reaper, its liveness predicate and its journal vocabulary — a different commission with a different gate profile. |
| **5** | **The urgent need is already met by this lane.** After D1 the hang cannot recur at that site; after D2 the stall **fails loudly from the shell side** (journal row + exit 78 + lock release) long before 660 s; after D3 the stall is **correlatable** (per-run log path + last phase marker). The daemon's row becomes a *secondary* observation of something the shell already reported. |

### 3.3 What this lane DOES change (the cheap, safe half)

### 3.2b The routed commission’s **MANDATORY scope** (three items) **[fold: A35, RULING 3]**

The routed board item must carry these three items **verbatim** — they are the architect’s + leader’s scope
boundary, not suggestions:

1. **A pre-txn stall signal** — a *"preflight started"* marker published **before** `journal_open_txn`. D3’s
   per-run log + phase markers are the **raw material**, but the marker must exist **before the txn**, not after
   (`journal_open_txn` is `promote.sh:327`; the daemon-side liveness predicate `_executor_alive_recent`,
   `daemon/services/upgrade_journal_sweep.py:2008`, keys on the **txn** heartbeat and is therefore blind to a
   pre-txn stall — the exact 2026-10-28 shape).
2. **The "stalled" predicate** — *no journal progress AND no heartbeat advance for N seconds* — **never a bare
   timer**. The 660 s threshold is a healthy-path number (490 s observed minimum + headroom,
   `daemon/services/upgrade_journal_sweep.py:98`-`:102`), not a stall number; a timer-kill would kill a
   legitimate slow promote.
3. **The writer-side journal-growth policy** — 89 % of the 455 KB journal is `mcp_server_state`
   (`daemon/mcp/init_guard.py:150`). D1’s size guard only converts the cliff into a **loud refusal**; the
   **growth itself** belongs to the writer’s owner (the same owner as the init-guard journal policy).

**Not in the routed scope:** the kill contract stays as it is in this lane (RULING 3). The routed commission may
*propose* a change; it does not inherit one from here.


| # | Change | Why it is safe |
|---|--------|----------------|
| **1** | The `executor_still_running` row gains the **per-run log path** and the **last known phase** (from the journal's own rows) ⇒ an operator reading the row can go straight to the file and see where the run stalled, instead of the current "correlate with data/upgrade.log" hand-off to a single shared file. | Purely additive observability; the row's meaning and the kill contract are unchanged. |
| **2** | The row's text stops implying the stall is benign-but-unknown: it states the **elapsed** time and the **last journal activity** timestamp, so "no progress for X s" is visible in the row itself. | Additive. |
| **3** | A **board item is opened** for the daemon-side escalation ("executor stall escalation: a pre-txn liveness signal + a stalled predicate"), with the two rejected alternatives (blind timer kill; keeping the current no-op) recorded. | The routing is explicit, not a silent drop. |

**Considered and rejected in-lane:** killing the unit when the shell has already journalled a `deadline-breach-*`
row — **unnecessary**: the shell's breach handler exits by itself (`exit 78` + lock release), so the unit
terminates on its own; a daemon-side kill would be a second, redundant mechanism.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| **T4.1** | **Reproduce the orphan** in the sandbox: a stage-like run acquires the lock and is SIGKILLed ⇒ the lock dir survives; a second acquisition reclaims it. | The sandbox shows the dir surviving the SIGKILL and the reclaim happening on the next acquisition. | test pack | P0 | — | AC1 |
| **T4.2** | **Journal the reclaim** (`lock-stale-break` row: reclaimed owner pid, reclaimed run_id, heartbeat age, reason, reclaiming run_id) at both break sites. | Both break paths write exactly one row with all five fields; the row lands in the journal (not only in the log). | `lib.sh:1670`-`:1694` | T4.1 | — | AC2 |
| **T4.3** | **Explicit success-path release** in `stage.sh` (idempotent; the `_LOCK_RELEASED` guard prevents a double release). | A successful stage leaves **no** lock dir; the EXIT trap does not double-release (no duplicate row). | `stage.sh:744` | — | — | AC3 |
| **T4.4** | **Enrich the `executor_still_running` row** with the per-run log path, the last known phase and the elapsed-since-last-journal-activity. | The row carries all three; the kill contract and the 660 s threshold are **unchanged** (asserted by a pin). | `daemon/services/upgrade_journal_sweep.py:2281`-`:2291` | P3 | R10 | AC4 |
| **T4.5** | **Open the routed board item** for the daemon-side stall escalation, recording the two rejected alternatives and the reason. | The board item exists with the rejected alternatives and the rationale; the lane's report links it. | board item | — | — | AC5 |
| **T4.6** | **Record the routed pipeline-hygiene question** (who SIGKILLed the stage run) in the lane report + project history. | The question is named with its owner and its evidence pointer. | lane report | — | — | AC6 |
| **T4.7** | **Open the routed board item** carrying the **three mandatory scope items** of §3.2b (pre-txn stall signal · the "stalled" predicate · the writer-side journal-growth policy), the two rejected alternatives (blind timer kill / keep the current no-op), and the in-lane correlatability that already ships (the enriched `executor_still_running` row). **[fold: A35]** | The board item exists with all three scope items verbatim, both rejected alternatives, and a pointer to the in-lane work that already landed; the lane report links it. | board item + lane report | T4.4 | R10 | AC7 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | A SIGKILLed lock-holder leaves the lock dir behind (reproduced in the sandbox), and the next acquisition reclaims it. |
| **AC2** | Every lock reclaim writes exactly one `lock-stale-break` journal row with the reclaimed owner pid, the reclaimed run_id, the heartbeat age, the reason and the reclaiming run_id — at both break sites. |
| **AC3** | A successful `stage.sh` run leaves **no** lock dir; the release happens exactly once (no duplicate row). |
| **AC4** | The `executor_still_running` row carries the per-run log path, the last known phase and the elapsed-since-last-activity; the 660 s threshold and the no-kill contract are **unchanged** (pinned). |
| **AC5** | A board item exists for the daemon-side stall escalation, with the rejected alternatives recorded. |
| **AC6** | The routed pipeline-hygiene question (the SIGKILL source) is recorded with an owner and an evidence pointer. |
| **AC7** | The routed board item carries the **three mandatory scope items** verbatim (pre-txn stall signal · the "stalled" predicate · the writer-side journal-growth policy) plus both rejected alternatives; the in-lane `executor_still_running` enrichment is referenced as already-shipped correlatability. |


---

## 6. Fold log (architect → Phase 4), 2026-10-29

| A# | Subject | Status | Where |
|----|---------|--------|-------|
| A35 | D4b routed board item gains the three mandatory scope items | folded | §3.2b, T4.7, AC7 |
| RULING 3 | The benign-detach kill-contract change stays **ROUTED** (leader ruling 3) | folded | §3.2 status banner, §3.2b, T4.7 |
| RULING 5 | The cgroup-wide-kill invariant names D4a as the owner of the SIGKILL-class orphan case | folded | §2.1 + §2.3 (already in T4.1) — no new task |

**Superseded:** the "if the leader rules fix-now" contingency text → **resolved** by the leader’s confirmation of
the routing (RULING 3); the reasoning is retained above as the record. **Already present:** D4a-1 (the corrected
framing — trap-only release + unjournaled reclaim), D4a-2 (the three-part fix), D4a-3 (the reclaim policy is
unchanged), D4a-4 (the routed pipeline-hygiene question) — all endorsed; the architect’s RULING 5 cross-reference
confirms that the **SIGKILL-class orphan case belongs to D4a**.
