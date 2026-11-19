# REVIEW PACKET — live wedge repair (amendment 2026-11-18)

**Purpose:** self-contained evidence for the leader's reviewer + approver chain.
**Artifact:** `repair/live_wedge_repair.py` + `repair/RUNBOOK.md` (both under
`.agents/shared/planning/wake-path-carrier-fix/repair/`).
**Target:** live instance `17efd2ef-9f98-46d4-b41b-60e3e32b2d13` (agent `ari`),
database `ensemble_prod`, daemon v0.31.0.
**Gate string (mandatory, byte-exact):** `wake-path-carrier-fix/live-wedge/17efd2ef/w8`
**Execution status:** **NOT EXECUTED. NOT AUTHORISED beyond the Emperor's
"Repair" (2026-11-18, Discord; board item wi-8f60e6f1).** This packet is a
proposal; execution is the daemon's gated repair path (3-factor), not this
script.

---

## (i) Emitter diff — `diff -u before/after`

Files are untracked, so `git diff` is EMPTY. The file diff is the evidence.

- Before copy: `repair/amendment/before/live_wedge_repair.py`
  sha256 `a564d67a179882f1efb63f498883ebc868c8c589f13e2786740c6500b0447fe3`
- After: `repair/live_wedge_repair.py`
  sha256 `26ae6f8e47f12369575a78ec8e05c78f243a241d08806ddb95755d30a8754b2d`
- Diff: `repair/reviewpacket/emitter_diff.patch` (308 lines)

The diff covers ONLY the amendment (the w8 widening is already in the base copy).
Changes: A1/A9 transient tolerance + re-read; A3/A6/A14 conditional on
disposition; needs_backfill eligibility mirroring the re-arm skip predicate;
drift-guard expected population derived from dispositions; receipt note +
amendment section; console disclosures. `--help` / gate / DO-block form untouched.

## (ii) Amended assertion list — per-relaxation justification

| assertion | what changed | why it is NOT an unknown world |
|---|---|---|
| A1 / A9 (parent status) | acceptable set `{waiting_children}` → `{waiting_children, running}`; a `running` observation is recorded and the instance is re-read ONCE (bound: exactly one re-read, no loop); any OTHER status or a missing parent still refuses | the parent alternates `running ⇄ waiting_children` on its hourly turn cycle (Appendix B measured a 17-minute `running` window). Mid-turn is a documented, self-resolving posture, not a defect. Refusing it would make the gate un-runnable while the Emperor works. |
| A3 (task 39320) | for a watcher still `PENDING`: missing source task → refuse. For an already-resolved (`CANCELLED`/`FIRED`) watcher: missing source task → PASS, annotated `source_task GC'd after resolution` | a resolved watcher's terminal state is recorded in the row itself; a resolved row can no longer fire (all three fire primitives match `state='PENDING'` only). The source task is no longer needed to prove anything; its GC is housekeeping. |
| A6 (task 39424) | same as A3 | same |
| A14 (task 39564) | same as A3, gated on BOTH W3 and W4 being resolved | same |
| A7 (4 commissions) | unchanged identity semantics | — |
| A8 (drift guard) | unchanged unknown-count semantics; expected numbers now derived from dispositions (PENDING watchers = 0, outstanding commissions = 2) | — |
| A2, A5, A10, A11, A12, A13 | unchanged identity | — |

## (iii) Per-row backfill verdict (with code cites)

Re-arm candidate predicate (`find_cancelled_unenqueued_for_target`,
`repository.py:873-918`): `state='CANCELLED' AND enqueued_at IS NULL`.
Re-arm skip (`instance_lifecycle.py:4125-4260`, ~`4229-4239`):
`if child.status in TERMINAL_STATUSES: continue`, where
`TERMINAL_STATUSES = {terminated, completed, error, failed}`
(`job_queue_service.py:126-131`); a missing child row is also skipped
(`child is None -> continue`).

| watcher | child | child status | verdict |
|---|---|---|---|
| W1 `8a1f60d7` | `542b49a6` (sonic-leader) | `completed` → TERMINAL | **skip-protected → NO backfill** |
| W2 `999efa71` | the parent itself | non-terminal | **re-arm eligible → backfill** |
| W3 `5602e91d` | `82551f59` (leader) | non-terminal | **re-arm eligible → backfill** |
| W4 `c83e12a2` | `82551f59` (leader) | non-terminal | **re-arm eligible → backfill** |

Backfill blocks are guarded `UPDATE … SET enqueued_at=now() WHERE watch_id=<full PK>
AND state='CANCELLED' AND enqueued_at IS NULL` in the same `DO $$` + `GET
DIAGNOSTICS` form (rowcount 0 or 1 both fine).

## (iv) REAL `--inspect` and `--emit-request` outputs

Full outputs: `repair/reviewpacket/inspect_output.txt` (exit 0) and
`repair/reviewpacket/emit_output.txt` (exit 0). Key lines:

Inspect (2026-11-18T18:32:36Z, `ensemble_prod`):
- `parent status = waiting_children`, `mid_turn = False`
- all 4 watchers `CANCELLED` / `naturally_resolved`; W1 no backfill, W2/W3/W4 `needs enqueued_at backfill`
- M1/M2 `abandoned` / `naturally_resolved`; M3/M4 `outstanding` / `in_scope_stale`
- A1–A14 all PASS; A3/A6/A14 pass as `source_task GC'd after resolution` (expected)
- A8: `pending_for_parent=0 (unknown=0), outstanding_for_parent=2 (unknown=0)`
- `classification = partial`; `count_pending_for_target(parent)=0`;
  `count_outstanding_commissions(parent)=2`
- derived expected population: PENDING watchers = 0, outstanding commissions = 2

Emit (exit 0, 5 statement blocks):
- 3 backfill blocks (W2, W3, W4) + 2 abandon blocks (M3, M4)
- `classification = partial`; `REQUEST WRITTEN (5 statement blocks) — NOT EXECUTED`
- emitted files (sha256):
  - `repair/emitted/live_wedge_repair_request__20261118T183256Z.sql`
    `64c250c7059e6e1d010f72a0fddeebaba1da6a326cc52080270bef30b82c9173`
  - `repair/emitted/live_wedge_repair_request__20261118T183256Z.receipt.md`
    `a9b1a202ee245ec074cb850bb538f4740d93f5bc223b5125369356b985e3cf9a`
  - `repair/emitted/live_wedge_repair_request__20261118T183256Z.receipt.json`
    `8f0da1c0d41318b613453a3d624ad9288c75be8cdffe363dd784150faa19e9e6`

Block list:
1. W2-backfill — `999efa71-8ce3-4fd5-b19e-1627e0018205` (stamp enqueued_at)
2. W3-backfill — `5602e91d-5d4c-4fb6-9e2b-8f351d203f52` (stamp enqueued_at)
3. W4-backfill — `c83e12a2-83b8-46ce-9213-f5a24a5060f6` (stamp enqueued_at)
4. M3 abandon — `06001f88-9e00-4720-8e59-88959d8faa75` — `ttl_expired_not_live`
   (worker `82551f59`, status `waiting_children`)
5. M4 abandon — `a1561f19-2338-4053-9b7d-ea85c9d92613` — `ttl_expired_not_live`
   (worker `82551f59`, status `waiting_children`)

Omitted (already resolved, guarded to affect 0 rows anyway): W1–W4 cancel, M1, M2.
No backfill for W1 (child terminal → skip-protected). No conditional backfill blocks
needed for M rows (commissions have no enqueued_at).

## (iv-bis) `--verify` PRE-repair (amended, exit 5)

```
[PASS] 0 PENDING watchers remain for the parent (mirrored count_pending_for_target)
        observed=0
[FAIL] 0 outstanding commissions remain for the parent (mirrored count_outstanding_commissions_for_anchor)
        observed=2
[PASS] parent row untouched BY THE REPAIR (no identity/creation field moved)
[PASS] recomputed PARENT-SCOPED inventory sha256 matches the receipt (no other parent-scoped row moved)
[FAIL] recomputed GLOBAL inventory sha256 matches the receipt (quiesced-system check — informational here)
        churn (expected on a live fleet)
```

Checks 1 and 4 PASS already (bus gate open; no parent-scoped row moved). Check 2 FAILs
because M3/M4 are still outstanding (the repair has not run). Check 5 (global) FAILs due
to live-fleet churn — informational, not a repair problem.

The receipt carries the verbatim RECEIPT NOTE and the amendment section (parent
posture, derived expected population, backfill eligibility, receipt note).

## (v) Emitted file list + sha256s

See (iv) — three files, canonical amended triple
`live_wedge_repair_request__20261118T183256Z.*`. A superseded earlier triple
(`…T010144Z.*`, pre-amendment) remains under `repair/emitted/` as audit trail;
verify uses the newest receipt automatically.

## (vi) Residual analysis

- **M3/M4 reason flip risk:** `ttl_expired_not_live` holds only while worker
  `82551f59` stays non-terminal. If it goes terminal before ceremony, lane-7
  semantics flip the reason to `worker_terminal_no_report`; the emitter
  re-derives the reason at emit from the live worker status and discloses the
  flip. Not silently flipped.
- **New rows:** A8 drift guard refuses and names them; scope never auto-extended.
- **Parent posture at ceremony:** tolerant of `running` or `waiting_children`;
  any other status refuses.
- **Lane 7 TTL backstop did NOT fire ~11 h past the 04:52/04:53Z expiry** for
  M3/M4. Possibly the same wiring inertness as the periodic sweep
  (`dependency_bus` constructed before `set_dependency_bus`, `api.py:1919-1920` vs
  `:5710`). Note only — not investigated, not part of this repair.
- **`verify` check 4 baseline is empty by construction** (all 8 targets excluded;
  no other parent-scoped rows). Meaning: "no NEW parent-scoped row appeared."
- **Cosmetic residuals intentionally untouched** (minimality): old 4-row fixture
  comment wording, `--verify` check-4 name, archived evidence capture.

## (vii) Verbatim RECEIPT NOTE + Emperor attestation

RECEIPT NOTE (verbatim, carried in the emitted receipt md + json + sql header):
"the wedge's BUS GATE was ALREADY OPEN (watchers cancelled by the boot sweep)
before this repair; the remaining hold was the mission leg's two commissions.
The Emperor's authorization is recorded on board item wi-8f60e6f1."

Emperor attestation: "Repair", 2026-11-18, Discord; board item wi-8f60e6f1.

## (viii) Execution preview (for the maintainer, not executed here)

Per-block, in order, one dry-run + one confirm each, as `maintenancer`
(sole holder of the `ens-db` category), with `ENSEMBLE_REPAIR_ENABLED` on and
a single-use nonce minted by each dry run:

1. `ens_db_repair_execute(sql=<block 1>, target_label="wake-path-carrier-fix/live-wedge/17efd2ef/w8#block1", dry_run=True)` → projected rowcount 1 + nonce
2. same with `confirm=True, dry_run=False, nonce=<nonce>` → commit
3. repeat for blocks 2..5 in file order
4. run `live_wedge_repair.py --verify --env-file /home/nea/agents-ensemble/.env`
   (expects: 0 PENDING, 0 outstanding, parent untouched, parent-scoped row diff
   empty)

Every block is `WHERE <full PK> AND state='CANCELLED'|'outstanding'`-guarded and
idempotent (0 rows ⇒ NOTICE no-op, never an error), so a partial application is
safe to resume. Blocks 1–3 stamp `enqueued_at` only; blocks 4–5 abandon M3/M4
with `ttl_expired_not_live`. `repair_log` carries `sql_class='DDL'` (no row image)
for these blocks — the compensating control is the inventory `sha256` /
parent-scoped row diff re-checked by `--verify`.

---

**No execution performed. No reviewer/approver spawned. No daemon edits. No
commits. Writes confined to `repair/`.**
