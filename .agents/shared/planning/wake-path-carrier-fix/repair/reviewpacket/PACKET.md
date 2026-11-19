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
| A1 / A9 (parent status) | **INSPECTION tolerates `running`** (recorded; exactly ONE re-read, no loop; any OTHER status or a missing parent refuses). **EMISSION intentionally requires a settled `waiting_children` window** — the classifier's `parent_ok` is still `status == "waiting_children"` (`live_wedge_repair.py:1109-1113`), so a mid-turn `running` observation PASSES the assertion but REFUSES the emit (fail-closed by design, council-affirmed). | the parent alternates `running ⇄ waiting_children` on its hourly turn cycle (Appendix B measured a 17-minute `running` window). Mid-turn is a documented, self-resolving posture for INSPECTION; emission deliberately waits for a settled dock so the proposal is built against a stable world. |
| A3 (task 39320) | for a watcher still `PENDING`: missing source task → refuse. For an already-resolved (`CANCELLED`/`FIRED`) watcher: missing source task → PASS, annotated `source_task GC'd after resolution` | a resolved watcher's terminal state is recorded in the row itself; a resolved row can no longer fire (all three fire primitives match `state='PENDING'` only). The source task is no longer needed to prove anything; its GC is housekeeping. |
| A6 (task 39424) | same as A3 | same |
| A14 (task 39564) | same as A3, gated on BOTH W3 and W4 being resolved | same |
| A7 (4 commissions) | unchanged identity semantics | — |
| A8 (drift guard) | unchanged unknown-count semantics; expected numbers now derived from dispositions (PENDING watchers = 0, outstanding commissions = 2) | — |
| A2, A5, A10, A11, A12, A13 | unchanged identity | — |

## (iii) Per-row backfill verdict (with code cites)

Re-arm candidate predicate (`find_cancelled_unenqueued_for_target`,
`repository.py:797-843`): `state='CANCELLED' AND enqueued_at IS NULL`.
Re-arm skip (`instance_lifecycle.py:4214-4246`):
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
- **New rows:** A8 drift guard **IGNORES advisory rows** (`advisory_backfill=true`) — it refuses ONLY on NON-advisory unknowns (a PENDING watcher / outstanding non-advisory commission outside the 8-row set). So a new advisory row is tolerated by design and does NOT block the emit; a new non-advisory row refuses and is named. Scope is never auto-extended.
- **Parent posture at ceremony:** INSPECTION tolerates `running` or `waiting_children`; EMISSION requires a settled `waiting_children` window (strict, `live_wedge_repair.py:1109-1113`, fail-closed by design and council-affirmed). Any other status refuses either way.
- **Lane 7 TTL backstop did NOT fire ~11 h past the 04:52/04:53Z expiry** for
  M3/M4. Possibly the same wiring inertness as the periodic sweep
  (`dependency_bus` constructed before `set_dependency_bus`, `api.py:1919-1920` vs
  `:5710`). Note only — not investigated, not part of this repair.
- **`verify` check 4 baseline is NOT empty:** the 3 advisory rows remain in the
  parent-scoped baseline and check 4 PASSES by sha256 match (the 8 targets are
  excluded; the advisory rows are not). Meaning: "no parent-scoped row moved
  outside the target set."
- **Cosmetic residuals intentionally untouched** (minimality): old 4-row fixture
  comment wording, `--verify` check-4 name, archived evidence capture.

## (vii) Verbatim RECEIPT NOTE + Emperor attestation

RECEIPT NOTE (verbatim, carried in the emitted receipt md + json + sql header):
"the wedge's BUS GATE was ALREADY OPEN (watchers cancelled by the boot sweep)
before this repair; the remaining hold was the mission leg's two commissions.
The Emperor's authorization is recorded on board item wi-8f60e6f1."

Emperor attestation: "Repair", 2026-11-18, Discord; board item wi-8f60e6f1.

**Receipt context — causal precondition:** the mission leg holds the park only under `ENSEMBLE_MISSION_RECORDS=enforce` (`daemon/manager.py:13931-13935` @ `eca98397d`; confirmed enabled in live `.env:117`). With the feature OFF the mission-leg read returns a real 0 and only the bus gate would matter.

## (viii) Execution handoff — council-corrected 2026-11-18

> **This handoff replaces the earlier "Execution preview". The emitted request
> (`…T183256Z.*`) is unchanged and sound; only this handoff text was wrong.**

**A. The corrected procedure (VERBATIM):**

1. Run EXCLUSIVELY as maintenancer with ENSEMBLE_REPAIR_ENABLED enabled; confirm parent 17efd2ef is not mid-crisis and worker 82551f59 is still non-terminal (if terminal, HALT and RE-EMIT, never edit SQL).
2. For each block N = 1..5 in file order from live_wedge_repair_request__20261118T183256Z.sql, paste ONLY the single `DO $$ ... $$;` block body (never a .sql filename):
   - `ens_db_repair_execute(sql=<block N body>, target_label="wake-path-carrier-fix/live-wedge/17efd2ef/w8#blockN", dry_run=True)` → expect sql_class='OTHER', preview skipped (NO projected rowcount), and a minted nonce valid for 300 seconds.
   - Within 300s: `ens_db_repair_execute(sql=<block N body>, target_label="wake-path-carrier-fix/live-wedge/17efd2ef/w8#blockN", dry_run=False, confirm=True, nonce=<nonce>)` → commit (repair_log.sql_class='OTHER'). If interrupted mid-sequence, safely resume from the next unapplied block.
3. Run `live_wedge_repair.py --verify --env-file /home/nea/agents-ensemble/.env` → checks 1-4 must PASS (check 5 global churn failure is informational).

**B. The factual corrections this handoff encodes (blocking amendments 1-4):**

| # | correction | citation |
|---|---|---|
| 1 | At `eca98397d`, `DO $$ ... $$;` classifies as **OTHER**, not DDL (`_classify_sql` first-word heuristic: the head token is `DO`, not in the DDL set). So `_dry_run_sqlite_safe` takes the **preview-skipped** branch: the dry run MINTS the nonce and returns **NO "projected rowcount 1"**; the first SQL parse/execution happens on `confirm=True`; `repair_log.sql_class` records **`'OTHER'`**. | `daemon/tools/ens_db_tools.py:267-293` (`_classify_sql`), `:1548-1552` (preview-skipped branch) |
| 2 | Nonce TTL = **300 s** (`REPAIR_NONCE_TTL_S`): each dry-run → confirm pair must complete within **5 minutes**. | `ens_db_tools.py:177`, `:557-561` |
| 3 | Executor session identity: run **strictly as `maintenancer`** (sole holder of `ens-db`). `dependency_watchers` / `mission_commissions` are NOT in `_SELF_SURGERY_TABLES`, so `_refuse_self_surgery` should not fire on blocks ④/⑤ — but councilors disagreed, so state it plainly: **EITHER WAY `maintenancer` is mandatory and safe; treat any self-surgery refusal as a WRONG-SESSION signal, not a repair failure.** | `_SELF_SURGERY_TABLES` `ens_db_tools.py:253-261`; `_refuse_self_surgery` `:461-468` |
| 4 | Input rules: pass ONLY the raw `DO $$ ... $$;` block BODY in `sql` (never a `*.sql` path — rejected by the migration-runner trap). One block per call is CALLER-ENFORCED (the tool runs one `sql` string per call). | `_SQL_FILENAME_RE` `ens_db_tools.py:222` |

> **Emitter docstring overstatement (deferred):** `_do_block`'s docstring still claims the
> executor classifies the block as DDL. That is the same overstatement — deferred to the
> NEXT emitter touch. **Do NOT edit the emitter now.**

**C. Pre/post ceremony guardrails (amendment 6):**

- **Pre-execution:** if worker `82551f59` goes terminal, or an unexpected **NON-advisory** commission appears, before execution → **RE-EMIT in a settled window — never hand-edit the SQL.** (The emitted file is immutable evidence.)
- **Post-execution:** `--verify` checks **1-4** must PASS. Check 5 (global inventory churn) failing is **informational only** and does not block.

**D. Advisory items recorded (no action now):**

- **Classifier-flag ruling:** the emission gate is an INTENTIONAL fail-closed design (strict settled-window requirement); this packet had overstated it (corrected in §(ii) and §(vi)). **No code change this ceremony.** A 1-line fix exists (drop `parent_mid_turn` from the classifier refusal) but is **NOT recommended**; on the parent's ~1 h cycle (~17-min `running` window) settled-window availability is ~1 h → re-emission refusal risk LOW.
- **FUTURE re-emission gap (blocking ONLY if ever re-emitting):** the classifier never reads `a["ok"]` (`live_wedge_repair.py:1422`, `:1776`) — wire `all(a["ok"] for a in assertions)` into the emission gate before ANY future re-emit, and refresh the selftest fixture run (latest transcript predates the amendment). **Not done now — pre-re-emission checklist item.**
- **Causal precondition:** the mission leg holds the park only under `ENSEMBLE_MISSION_RECORDS=enforce` (`daemon/manager.py:13931-13935` @ `eca98397d`; confirmed enabled in live `.env:117`).
- **Lane-7 wiring hypothesis** (`api.py:1919-1920` vs `:5710`): a **SEPARATE bug investigation OUTSIDE this repair** — recorded, not investigated.
- **Anchor corrections applied:** `find_cancelled_unenqueued_for_target` = `repository.py:797-843` (not `:873-918`); re-arm skip = `instance_lifecycle.py:4214-4246`; `transition_commission_state` = `mission_record/repository.py:724-771`.

---

**No execution performed. No reviewer/approver spawned. No daemon edits. No
commits. Writes confined to `repair/`.**
