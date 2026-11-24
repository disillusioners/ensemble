# Runbook / Proposal: the checkpoint ESCALATION lane (a safe reclaim)

**Component:** the proposal for the **scheduled/weekly lane** that gets the
checkpoint store's *space* back — the follow-on to the retention lane
(`daemon/services/maintenance.py`'s Op B2 terminal-thread reap + the
backstop job).
**Code owners:** none yet — this is a PROPOSAL. The daemon-side
contributions are (a) the bytes-based occupancy guard
(`_collect_blob_bytes_guard` → the `blob_bytes_guard` block in
`maintenance_runs.summary_json`) and (b) the retention/churn legs that
relieve the GROWTH.
**Risk class:** HIGH. Everything in §3 is a lock-hazard class: the
measured lock-stall facts below are the reason the lane is
**operator-nudged** and never automatic.

> **THE PROMISE IS BINDING.** This lane's promise is **"relieve
> growth"**. It is NEVER "return the space". See §4 for the measured
> proof and §5 for the honest inventory.

> **Sourcing legend.** `[measured 2026-11-23]` = the read-only census of
> the prod PG18 host during the post-outage recovery commission.
> `[measured 2026-11-24]` = the read-only checkpoint census taken while
> implementing this lane. `[POK checkpoint-lock-stall-behavior]` = the
> recorded operating fact (the checkpoint save path's timeout/lock
> behaviour). `[house]` = standard PostgreSQL operator mechanics added to
> operationalize a rule — no new system facts asserted.

---

## 1. What the lane is for (and what it is NOT)

The retention lane (§5) bounds the GROWTH: it keeps the per-thread
history at keep-N, reaps the checkpoint footprint of finished work, and
bounds the blob mass that the live rows carry.

The ESCALATION lane is the follow-on that addresses the SPACE: the
~39GiB of TOAST the prod relation was carrying is **not** live data that
a retention rule can reclaim — it is the physical footprint of a table
whose live rows are large, and it returns only via a **rewrite**.

**RULE 1.1 — the lane is OPERATOR-NUDGED, never automatic.** The weekly
lane may *report* that the reclaim is due and *propose* the command; it
must NEVER issue it on its own. The operator runs the command, in a
window they chose, after the pre-flight checks in §3 pass.

**RULE 1.2 — the lane NEVER runs `VACUUM FULL`.** It is excluded by
construction and by policy:

- The daemon-side escalation is already structurally incapable of it:
  `.agents/shared/planning/maintenance-tabs-vacuum/decisions.md` **§D-4**
  pins the guard in FOUR fail-closed layers (a MANUAL-ONLY method, an
  explicit kwarg, an AST pin that the auto cycle does not call it, and a
  second AST pin that the method is defined exactly once, on
  `CheckpointCleanupJob` only). The weekly lane inherits that posture:
  anything the lane proposes must remain MANUAL-ONLY.
- `VACUUM FULL` takes `ACCESS EXCLUSIVE` on the table and **rewrites the
  whole relation**. On `checkpoint_blobs` — the table carrying the TOAST
  mass — that is a multi-hour exclusive lock on a table the checkpoint
  save path writes continuously. | [house]

---

## 2. The measured lock-stall facts (why "never automatic" is not paranoia)

| Fact | Source |
|---|---|
| The checkpoint save path sets **NO `statement_timeout` and NO `lock_timeout`** — there is no `SET` on the path. | [POK checkpoint-lock-stall-behavior] |
| The saver's single `asyncio.Lock` caps save concurrency at **1**. | [POK checkpoint-lock-stall-behavior] |
| The pool is **max 10** with a **5 s** acquire timeout. | [POK checkpoint-lock-stall-behavior] |
| ⇒ An **`ACCESS EXCLUSIVE` lock on `checkpoint_blobs` stalls turns for its duration** — the write path waits on the lock with no timeout of its own. | [POK checkpoint-lock-stall-behavior] |

**RULE 2.1 — no lane action may hold a lock on `checkpoint_blobs` (or
`checkpoints`) while the daemon is serving traffic.** That includes
`VACUUM FULL`, and it includes a rewrite that takes an exclusive lock
for its duration without a bounded timeout.

**RULE 2.2 — the lane's own commands must set their timeouts.** Any
statement the lane issues must carry an explicit `lock_timeout` /
`statement_timeout` so it can never become the thing that stalls the
daemon.

---

## 3. The safe reclaim: the candidates, in order

| Candidate | What it does | No-downtime? | Status |
|---|---|---|---|
| **Plain `VACUUM (ANALYZE, VERBOSE)`** | Makes the dead space reusable; **does NOT shrink the file**. | **YES** — the no-downtime proof was met (see §4). | The lane's DEFAULT. |
| **`pg_repack`** | Rewrites the table and swaps it in, with a bounded exclusive lock per phase. | Yes (by design — the no-downtime path). | **The no-downtime rewrite path.** `pg_repack` is **UNAVAILABLE in the prod image** (Photon 5.0) — **installing it is the Emperor's decision**, not the lane's. |
| **`VACUUM FULL`** | Rewrites the table in place under an exclusive lock. | **NO** — it blocks every read and write for its duration. | **EXCLUDED** (§1.2). |

**RULE 3.1 — the lane's proposal is `pg_repack`, and it PROPOSES — it does
not install and does not run.** The `pg_repack` install is the Emperor's
decision; the lane may only say "the reclaim is due, and the no-downtime
path is `pg_repack`".

**RULE 3.2 — before ANY rewrite-shaped action, the volume's headroom is
checked first.** The rewrite EXTENDS past the historical high-water
(new segment files) — so a volume at > 99 % of `spec.size`, or a
filesystem with < 4 GiB free, ABORTS the action. Those thresholds and
the read path are in `docs/runbooks/checkpoint-volume-bytes.md` §2.

---

## 4. The measured no-downtime proof (plain VACUUM)

| Observation | Source |
|---|---|
| A plain `VACUUM (ANALYZE, VERBOSE)` **completed at 19:32:01Z** with **the file NOT shrunk**. | [measured 2026-11-23] |
| **Checkpoint writes continued THROUGHOUT** — the no-downtime property held. | [measured 2026-11-23] |
| The ~38–39GiB is **TOAST space inside LIVE rows** — it is not dead space waiting to be reused. | [measured 2026-11-23] |

**RULE 4.1 — plain `VACUUM` is the no-downtime lane, and it PROMISES
NOTHING about the file size.** Its contract is "make the space
reusable"; its measured outcome on this class was "the file did not
shrink, and the writes never stopped". It must never be reported as a
reclaim.

**RULE 4.2 — the only thing that returns the space is a REWRITE**
(`pg_repack` — the no-downtime path; `VACUUM FULL` — excluded). Any
report, receipt, or dashboard that says "the space was reclaimed" must
name the rewrite that did it.

---

## 5. The honest inventory

| Dimension | Owner | What it delivers |
|---|---|---|
| **GROWTH** | **This commission's retention lane** (Op B2 terminal-thread reap + the backstop job + the keep-N cap). | The retention/churn bounds: the live mass stops ratcheting up. |
| **SPACE** | **The Emperor's decision** (the `pg_repack` install; the rewrite window). | The ~38GiB comes back — but only via a rewrite, and only if the install is approved. |
| **VISIBILITY** | **This lane's bytes guard** (`blob_bytes_guard` in `maintenance_runs.summary_json`) + the volume-bytes runbook. | A bytes floor + an alert + honest receipts — so the operator learns about the pressure BEFORE the volume is at risk. |

**RULE 5.1 — no component of this commission claims to have returned
space.** The retention lane relieves GROWTH; the bytes guard provides
VISIBILITY; the SPACE is a separate, operator-owned decision.

**RULE 5.2 — the visibility signal is BYTES, never dead tuples.** The
bloat class is dead-tuple-BLIND: the measured incident found
`n_dead_tup` reading **ZERO** on BOTH `checkpoint_blobs` and its TOAST
relation while the relation carried ~39.1GiB of TOAST (~87 % of the TOAST
pages empty) and autovacuum ran on it without reclaiming. | [measured
2026-11-23] The guard therefore measures `pg_total_relation_size` +
the TOAST size via `reltoastrelid` — the same SQL the house runbook
uses (`docs/runbooks/checkpoint-maintenance.md` §P6b).

**RULE 5.3 — the receipts are honest.** The `blob_bytes_guard` block
reports the bytes it measured and the threshold it compared against. It
does not report a reclaim, and it does not report the volume's
occupancy (that is the volume lane's measurement — see
`docs/runbooks/checkpoint-volume-bytes.md`).

---

## 6. The lane's proposed shape (for the leader's adjudication)

1. **Weekly, externally armed, operator-nudged.** The lane reports; the
   operator acts. No daemon-side kubectl, no daemon-side rewrite.
2. **The daemon side** contributes exactly two things: the
   `blob_bytes_guard` block (bytes + threshold + alert) and the
   retention legs that relieve the growth.
3. **The proposal text** names the reclaim that is due, the reason
   (bytes over threshold), the no-downtime path (`pg_repack`), the
   pre-flight checks (volume headroom, timeouts), and — explicitly —
   that the promise is "relieve growth", never "return the space".
4. **The install of `pg_repack`** is escalated as a decision, not taken.
5. **Nothing in the lane holds a lock on the checkpoint tables** while
   the daemon serves traffic; every statement carries an explicit
   timeout.

---

## 7. Cross-references

- `docs/runbooks/checkpoint-volume-bytes.md` — the volume-bytes lane (the
  Longhorn CRD read path, the thresholds, the minimal-SA note).
- `docs/runbooks/checkpoint-maintenance.md` — the in-database reclaim
  mechanics (Op D retention, Op E blob prune, the TOAST hygiene audit,
  the `VACUUM FULL` policy).
- `docs/runbooks/checkpoint-blob-prune-restore.md` — the Op E restore
  path.
- `.agents/shared/planning/maintenance-tabs-vacuum/decisions.md` §D-4 —
  the four-layer guard that makes the daemon structurally incapable of
  escalating to `VACUUM FULL`.
- `daemon/services/maintenance.py` — the retention lane (Op B2 + the
  backstop) and the bytes guard.
- `daemon/services/checkpoint_prune.py` — the Op E anti-join blob prune
  (the zero-refs fail-safe + the chunked, bounded delete).
